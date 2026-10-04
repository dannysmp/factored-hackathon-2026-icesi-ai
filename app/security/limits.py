"""
Attempt Limiter
===============

Overview
--------
Slows down repeated failed attempts to authenticate: after a set number of failures from one
client within a window, further attempts are refused until the window passes.

Scope
-----
In: counting failures per client key and refusing when the limit is reached.
Out: choosing the key (the route uses the client address) and reporting the refusal.

Design Principles
-----------------
- The clock is injected and the table is bounded, so a flood of distinct clients cannot exhaust
  memory; the oldest entries are dropped first.
- This limiter only ever counts what its caller reports as a failure: it has no notion of a
  success, and nothing here clears a key's count except the window passing. A caller that
  decides an attempt succeeded simply never calls ``begin_attempt`` for it, so a success can
  never erase another client's recorded failures on a key they share (for example, one client
  address behind a proxy).

Runtime Contract
----------------
``AttemptLimiter.begin_attempt(key) -> int`` is 0 when an attempt may proceed (and counts it as a
failure right away), otherwise the seconds to wait. Counting first makes the check and the count
one step, so concurrent attempts cannot all pass the check before any is counted.

Limitations
-----------
In memory and per process: with several processes the effective limit is multiplied, and a
restart clears it. The key is what the caller passes (the sign-in routes use ``client_address``:
the reverse proxy's last X-Forwarded-For entry, else the connecting address). When the table is
full the oldest key is dropped first, so a flood of distinct clients can evict a blocked one. A
shared store replaces it when the service runs as more than one process.
"""

from __future__ import annotations

# Standard libraries
import math  # Round waiting time up to whole seconds
import threading  # Shared between request threads
from datetime import datetime, timedelta  # Window arithmetic

# Local modules
from app.security.sessions import Clock, utc_now  # Injected clock

MAX_TRACKED_CLIENTS = 10_000


class AttemptLimiter:
    """Counts failures per key in a sliding window."""

    def __init__(
        self,
        max_failures: int = 5,
        window_seconds: int = 60,
        *,
        clock: Clock = utc_now,
        capacity: int = MAX_TRACKED_CLIENTS,
    ) -> None:
        self._max = max_failures
        self._window = timedelta(seconds=window_seconds)
        self._clock = clock
        self._capacity = capacity
        self._failures: dict[str, list[datetime]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: datetime) -> list[datetime]:
        """Failures of ``key`` still inside the window (caller holds the lock)."""
        recent = [moment for moment in self._failures.get(key, []) if now - moment < self._window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def begin_attempt(self, key: str) -> int:
        """Count an attempt by ``key`` and say how long to wait; 0 means it may proceed.

        Call this only for an attempt the caller has already decided is a failure: the attempt
        is counted as one immediately, and there is no way to take the count back.
        """
        now = self._clock()
        with self._lock:
            recent = self._recent(key, now)
            if len(recent) >= self._max:
                release = recent[0] + self._window
                return max(1, math.ceil((release - now).total_seconds()))
            if key not in self._failures and len(self._failures) >= self._capacity:
                self._failures.pop(next(iter(self._failures)))
            self._failures[key] = [*recent, now]
            return 0
