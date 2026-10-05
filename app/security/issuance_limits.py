"""
Issuance Limiter
================

Overview
--------
Bounds concurrent successful demo sign-ins per key — a client address, the whole broker, or one
persona slot. A reservation counts against its key's cap for exactly as long as the
session it backs would remain active, and ages out on that same schedule; a sign-out frees it
earlier.

Scope
-----
In: reserving capacity by key, against a cap and a TTL the caller supplies per call, and
remembering which reservations an issued session holds so a sign-out can free them.
Out: choosing the keys or caps (the demo broker route decides those); the wrong-access-code path,
which stays on ``AttemptLimiter`` — a failure-only, never-reset counter with a different contract
this module does not repurpose.

Design Principles
------------------
- **Counts successes, not failures** (the opposite of ``AttemptLimiter``): a caller reserves
  capacity only once it has already decided to issue a session, never speculatively. Mixing the
  two contracts in one class is deliberately avoided: ``AttemptLimiter`` stays safe only while it
  counts nothing but a reported failure.
- One key, one cap: the same class is used three times by the caller — once per client address,
  once for the whole broker, once per persona slot — each with its own cap and TTL, rather than
  three purpose-built classes for what is structurally the same operation.
- The clock is injected and the table is bounded, matching ``AttemptLimiter``'s own approach to
  memory safety under a flood of distinct keys.

Runtime Contract
-----------------
``IssuanceLimiter.try_reserve(key, cap, ttl) -> datetime | None``: the reservation's own expiry
when ``key`` was under ``cap``; ``None`` means it was already at ``cap`` and nothing was reserved.
``IssuanceLimiter.release(key, expiry)`` undoes exactly the reservation ``try_reserve`` returned
that expiry for — used when a caller checks several keys for one attempt and a later one refuses,
so an earlier successful reservation is not left burning capacity for an attempt that never
completed.

``SessionReservations(limiter).hold(session_id, reserved)`` remembers which reservations an issued
session holds; ``.release(session_id)`` frees exactly those, once. A broker registers it with
``SessionService.on_revoked``, so a person who signs out frees their profile at once instead of
leaving it held for the session's whole lifetime.

Limitations
-----------
The limiter itself has no view of revocation, only of how long a session was minted to live. A
session that ends any other way than an explicit sign-out (it expires unused, or is revoked by
another path) keeps its reservations until the original TTL elapses. The record of which
reservations a session holds is in-process and bounded: when it is full the oldest entry is
dropped, and that session's reservations then age out on their own TTL like any other.
"""

from __future__ import annotations

# Standard libraries
import threading  # Shared between request threads
from collections.abc import Sequence  # Reservations a session holds
from datetime import datetime, timedelta  # Window arithmetic

# Local modules
from app.security.sessions import Clock, utc_now  # Injected clock

MAX_TRACKED_KEYS = 10_000


class IssuanceLimiter:
    """Bounds how many live reservations one key may hold at once."""

    def __init__(self, *, clock: Clock = utc_now, capacity: int = MAX_TRACKED_KEYS) -> None:
        self._clock = clock
        self._capacity = capacity
        self._reservations: dict[str, list[datetime]] = {}
        self._lock = threading.Lock()

    def _live(self, key: str, now: datetime) -> list[datetime]:
        """Reservations of ``key`` not yet expired (caller holds the lock)."""
        live = [expiry for expiry in self._reservations.get(key, []) if expiry > now]
        if live:
            self._reservations[key] = live
        else:
            self._reservations.pop(key, None)
        return live

    def try_reserve(self, key: str, cap: int, ttl: timedelta) -> datetime | None:
        """Reserve one slot for ``key`` until ``ttl`` from now, if ``key`` is under ``cap``.

        Checking and reserving is one atomic, locked step, so concurrent callers for the same key
        cannot all pass the check before any of them is counted.

        Returns
        -------
        datetime | None
            The reservation's own expiry, to pass to :meth:`release` if a later check for the
            same attempt refuses; ``None`` if ``key`` was already at ``cap``.
        """
        now = self._clock()
        with self._lock:
            live = self._live(key, now)
            if len(live) >= cap:
                return None
            if key not in self._reservations and len(self._reservations) >= self._capacity:
                self._reservations.pop(next(iter(self._reservations)))
            expiry = now + ttl
            self._reservations[key] = [*live, expiry]
            return expiry

    def release(self, key: str, expiry: datetime) -> None:
        """Undo the reservation ``try_reserve`` made for ``key`` expiring at ``expiry``.

        A no-op if that exact reservation is not present (it may have already aged out) — this
        is a best-effort cleanup for an attempt that did not complete, not a guarantee.
        """
        with self._lock:
            entries = self._reservations.get(key)
            if entries is None:
                return
            try:
                entries.remove(expiry)
            except ValueError:
                return
            if not entries:
                self._reservations.pop(key, None)


class SessionReservations:
    """Remembers the reservations each issued session holds, so ending it can free them.

    Releasing is by the exact ``(key, expiry)`` pair the limiter handed out, and the record is
    removed in the same locked step that reads it: a second release for the same session finds
    nothing, and a reservation another session has since taken cannot be freed by mistake.
    """

    def __init__(
        self,
        limiter: IssuanceLimiter,
        *,
        clock: Clock = utc_now,
        capacity: int = MAX_TRACKED_KEYS,
    ) -> None:
        self._limiter = limiter
        self._clock = clock
        self._capacity = capacity
        self._held: dict[str, list[tuple[str, datetime]]] = {}
        self._lock = threading.Lock()

    def hold(self, session_id: str, reserved: Sequence[tuple[str, datetime]]) -> None:
        """Record that ``session_id`` holds ``reserved`` (each ``(key, expiry)`` as reserved)."""
        now = self._clock()
        with self._lock:
            if len(self._held) >= self._capacity:
                self._held = {
                    sid: held
                    for sid, held in self._held.items()
                    if any(expiry > now for _, expiry in held)
                }
            if len(self._held) >= self._capacity:
                self._held.pop(next(iter(self._held)))
            self._held[session_id] = list(reserved)

    def release(self, session_id: str) -> None:
        """Free every reservation ``session_id`` holds; a no-op for an unknown or released one."""
        with self._lock:
            held = self._held.pop(session_id, [])
        for key, expiry in held:
            self._limiter.release(key, expiry)
