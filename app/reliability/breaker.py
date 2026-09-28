"""
Circuit Breaker
================

Overview
--------
Stops calling a dependency that has just failed repeatedly, for a bounded cool-down, rather than
letting every subsequent turn pay the same timeout while it stays down. One breaker instance per
external dependency (the LLM provider, the serving store), never per method or per tool: every
call through a dependency shares its own fate.

Scope
-----
In: the three-state machine (closed, open, half-open) and its in-memory implementation.
Out: deciding what counts as a failure for a given call (``app.reliability.retry``'s adapters), the
retry loop itself.

Design Principles
-----------------
- **Built once, shared across every turn.** State that resets every request can never trip; this
  is exactly why the composition root builds one instance per dependency at start-up, not inside
  the turns route's own per-request factory (see ``app/main.py``'s own Design Principles).
- **In-memory and lock-guarded, matching ``InMemoryRevocationStore``'s own shape**
  (``app.security.sessions``): correct for this project's single-host, single-worker deployment
  (ADR-9, ADR-13); a shared store is the explicit trigger for the day this stops holding, not
  something to build ahead of need.
- **One trial call while half-open**, not a flood: the first ``allow()`` call after the cool-down
  elapses is let through and marks a trial in flight; every other caller is refused until that
  trial's own outcome is recorded, so a recovering dependency is not immediately hit with every
  queued request at once.
- **The clock is injected**, never read from the system directly, matching every other timed
  component in this codebase.

Runtime Contract
----------------
``CircuitBreaker`` (protocol): ``allow() -> bool``, ``record_success() -> None``,
``record_failure() -> None``.
``InMemoryCircuitBreaker(failure_threshold, reset_seconds, *, clock=utc_now)``.
"""

from __future__ import annotations

# Standard libraries
import threading  # Shared across concurrent requests in one process
from datetime import datetime  # Instant a breaker opened, for the cool-down check
from typing import Literal, Protocol

# Local modules
from app.security.sessions import Clock, utc_now


class CircuitBreaker(Protocol):
    """Where a caller checks whether a dependency is worth trying right now."""

    def allow(self) -> bool:
        """Whether a call may be attempted now."""
        ...

    def record_success(self) -> None:
        """A call just attempted (``allow()`` returned ``True`` for it) succeeded."""
        ...

    def record_failure(self) -> None:
        """A call just attempted (``allow()`` returned ``True`` for it) failed."""
        ...


_State = Literal["closed", "open", "half_open"]


class InMemoryCircuitBreaker:
    """A ``CircuitBreaker`` held in process memory, guarded by one lock."""

    def __init__(
        self, failure_threshold: int, reset_seconds: float, *, clock: Clock = utc_now
    ) -> None:
        self._failure_threshold = failure_threshold
        self._reset_seconds = reset_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._state: _State = "closed"
        self._consecutive_failures = 0
        self._opened_at: datetime | None = None

    def allow(self) -> bool:
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "half_open":
                # A trial is already in flight; every other caller waits for its outcome.
                return False
            assert self._opened_at is not None  # noqa: S101 - only "open" sets this
            elapsed = (self._clock() - self._opened_at).total_seconds()
            if elapsed < self._reset_seconds:
                return False
            self._state = "half_open"
            return True

    def record_success(self) -> None:
        with self._lock:
            self._state = "closed"
            self._consecutive_failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            if self._state == "half_open":
                self._state = "open"
                self._opened_at = self._clock()
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._state = "open"
                self._opened_at = self._clock()
