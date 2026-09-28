"""
Issuance Limiter
================

Overview
--------
Bounds concurrent successful demo sign-ins per key — a client address, the whole broker, or one
persona slot (ADR-18). A reservation counts against its key's cap for exactly as long as the
session it backs would remain active, and ages out on that same schedule: no separate "session
ended" event is needed, since a reservation's own expiry already matches the session's.

Scope
-----
In: reserving capacity by key, against a cap and a TTL the caller supplies per call.
Out: choosing the keys or caps (the demo broker route decides those); the wrong-access-code path,
which stays on ``AttemptLimiter`` — a failure-only, never-reset counter with a different contract
this module does not repurpose.

Design Principles
------------------
- **Counts successes, not failures** (the opposite of ``AttemptLimiter``): a caller reserves
  capacity only once it has already decided to issue a session, never speculatively. Mixing the
  two contracts in one class was rejected on purpose: the fix that made ``AttemptLimiter`` safe
  (issues #30/#31) depends on it never counting anything but a reported failure.
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

Limitations
-----------
A session ended early (logout, revocation) does not free its reservation before the original TTL
elapses: this limiter has no view of revocation, only of how long a session was minted to live.
Given the concurrency caps exist to bound cost and load, not to track exact session state, holding
a slot until the mint's own TTL naturally passes is judged an acceptable simplification over
wiring a release path through revocation.
"""

from __future__ import annotations

# Standard libraries
import threading  # Shared between request threads
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
