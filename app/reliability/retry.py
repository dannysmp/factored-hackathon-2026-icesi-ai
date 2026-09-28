"""
Bounded Retry
=============

Overview
--------
A small, exponential-backoff-with-full-jitter retry loop, and the LLM-side adapter built from it:
``RetriedLlmClient``, an ``LlmClient`` that retries a transient (``LlmUnavailable``) failure a
bounded number of times and refuses instantly while its circuit breaker is open, never retrying a
permanent failure (``LlmRequestRejected``, ``LlmOutputInvalid``).

Scope
-----
In: ``RetryPolicy``, the backoff calculation, ``RetriedLlmClient``.
Out: the breaker's own state machine (``app.reliability.breaker``), the tool-port adapter
(``app.reliability.tool_port`` — a value-returning call, not a raised exception, needs a
structurally different loop).

Design Principles
-----------------
- **Only a transient failure is retried.** ``LlmRequestRejected`` (bad credentials, a malformed
  request) and ``LlmOutputInvalid`` (the model's own output did not parse) fail exactly as fast as
  they do today; retrying either would waste a bounded budget on a failure retrying cannot fix.
- **Every outcome of an allowed call reports back to the breaker, not only a retried failure.**
  ``LlmRequestRejected``/``LlmOutputInvalid`` mean the provider was reached and answered — a
  reachability success for the breaker's own purpose, whatever it means for the caller — so both
  record success too. Recording only the retried-failure type would leave a half-open trial call
  that resolves to either of these permanently unresolved: ``allow()`` refuses every call after a
  half-open trial until its outcome is recorded, so a dependency that is actually back up would
  stay wrongly tripped open forever.
- **The inner client is built lazily, once, under a lock.** Deferring construction (and the
  ``ConfigError`` a missing provider key raises) to the first actual call keeps the composition
  root's own lazy-resolution guarantee: this wrapper can be built at start-up, unconditionally,
  without requiring an LLM provider key until a turn that actually needs one arrives. The build is
  lock-guarded because the turns route is a synchronous handler FastAPI dispatches on its thread
  pool, so the first calls from two concurrent requests can race; the lock only ever guards the
  one-time build, never a request's own call.
- **The circuit breaker is checked before any attempt**, not just recorded after one: an open
  circuit costs nothing, not even the first attempt's own timeout.
- **The sleep between attempts is injected**, so a test proves the retry count and the breaker
  transitions without a real delay.

Runtime Contract
----------------
``RetryPolicy(max_attempts, base_delay_ms, max_delay_ms)``.
``RetriedLlmClient(build_inner, *, policy, breaker, sleep=time.sleep)`` implementing ``LlmClient``.
"""

from __future__ import annotations

# Standard libraries
import logging  # Retry attempts, matching the codebase's own structured-logging convention
import random  # Full jitter between attempts
import threading  # Guards the lazy inner-client build against a concurrent first call
import time  # The real delay between attempts
from collections.abc import Callable
from dataclasses import dataclass, field

# Local modules
from app.llm.client import (
    CompletionRequest,
    CompletionResult,
    LlmClient,
    LlmError,
    LlmUnavailable,
)
from app.reliability.breaker import CircuitBreaker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many attempts a bounded retry gets, and how long it waits between them."""

    max_attempts: int
    base_delay_ms: int
    max_delay_ms: int


def backoff_seconds(policy: RetryPolicy, attempt: int) -> float:
    """Full-jitter exponential backoff before retrying ``attempt`` (1-indexed, the attempt that
    just failed)."""
    cap_ms = min(policy.max_delay_ms, policy.base_delay_ms * (2 ** (attempt - 1)))
    return random.uniform(0, cap_ms) / 1000  # noqa: S311 - jitter timing, not a security use


@dataclass(slots=True)
class RetriedLlmClient:
    """An ``LlmClient`` that retries ``LlmUnavailable`` with backoff, behind a circuit breaker."""

    build_inner: Callable[[], LlmClient]
    policy: RetryPolicy
    breaker: CircuitBreaker
    sleep: Callable[[float], None] = time.sleep
    _inner: LlmClient | None = field(default=None, init=False, repr=False)
    _build_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _inner_client(self) -> LlmClient:
        if self._inner is None:
            with self._build_lock:
                if self._inner is None:
                    self._inner = self.build_inner()
        return self._inner

    def complete(self, request: CompletionRequest) -> CompletionResult:
        """Raises ``LlmUnavailable`` immediately when the breaker is open, otherwise as the inner
        client's own ``complete`` does, after this policy's attempts are exhausted."""
        if not self.breaker.allow():
            raise LlmUnavailable("the circuit breaker is open")
        attempt = 0
        while True:
            attempt += 1
            try:
                result = self._inner_client().complete(request)
            except LlmUnavailable:
                if attempt >= self.policy.max_attempts:
                    self.breaker.record_failure()
                    raise
                logger.warning(
                    "llm_retry_attempt attempt=%s max_attempts=%s",
                    attempt,
                    self.policy.max_attempts,
                )
                self.sleep(backoff_seconds(self.policy, attempt))
                continue
            except LlmError:
                # The provider was reached and answered — a rejection or unparsable output is
                # not an outage, so this still resolves an in-flight half-open trial as success.
                self.breaker.record_success()
                raise
            self.breaker.record_success()
            return result
