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
- A successful attempt clears the client's failures.

Runtime Contract
----------------
``AttemptLimiter.retry_after(key) -> int`` is 0 when an attempt is allowed, otherwise the
seconds to wait. ``record_failure(key)`` and ``reset(key)`` update the count.

Limitations
-----------
In memory and per process: with several processes the effective limit is multiplied, and a
restart clears it. A shared store replaces it when the service runs as more than one process.
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

    def retry_after(self, key: str) -> int:
        """Seconds until an attempt is allowed again; 0 when it is allowed now."""
        now = self._clock()
        with self._lock:
            recent = self._recent(key, now)
            if len(recent) < self._max:
                return 0
            release = recent[0] + self._window
        return max(1, math.ceil((release - now).total_seconds()))

    def record_failure(self, key: str) -> None:
        """Count one failed attempt by ``key``."""
        now = self._clock()
        with self._lock:
            recent = self._recent(key, now)
            if key not in self._failures and len(self._failures) >= self._capacity:
                self._failures.pop(next(iter(self._failures)))
            self._failures[key] = [*recent, now]

    def reset(self, key: str) -> None:
        """Forget the failures of ``key`` after a success."""
        with self._lock:
            self._failures.pop(key, None)
