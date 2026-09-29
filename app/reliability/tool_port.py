"""
Bounded Retry for the Tool Port
================================

Overview
--------
``RetriedToolPort``: a ``ToolPort`` that retries a retryable ``ToolFailure`` with backoff, behind
one circuit breaker shared across every method — the serving store is one dependency, not six, and
every method fails together on the same outage.

Scope
-----
In: retrying and breaking on the value ``ToolFailure`` a wrapped port returns.
Out: the port it wraps (``app.persistence.reads.PostgresToolPort``), whose own audit-fail-closed
ordering and permission logic this module never touches — it wraps from the outside, one call at a
time.

Design Principles
-----------------
- **Only an infrastructure failure is retried.** ``ToolFailure.retryable`` already distinguishes an
  infrastructure failure (the store could not be reached) from a correctness outcome (a foreign or
  missing reference, refused by design, `retryable=False`); retrying the latter would be a bug, not
  a resilience improvement. A failure whose own ``cause`` is already ``"circuit_open"`` is never
  retried either — retrying the breaker's own signal would defeat it.
- **Every outcome of an allowed call reports back to the breaker, not only a retried failure.** A
  non-retryable ``ToolFailure`` (a permission refusal, a missing reference) means the store was
  reached and answered — a reachability success for the breaker's own purpose, whatever it means
  for the caller — so it records success too, exactly like a real value or ``None``. The wrapped
  port can also raise directly rather than return a ``ToolFailure`` — ``PostgresToolPort`` does
  this for an audit-write failure, by design (fail closed) — and that outcome records a failure
  before propagating, for the identical reason: recording nothing at all would leave a half-open
  trial call permanently unresolved just as surely as recording nothing for a known outcome would.
  ``allow()`` refuses every call after a half-open trial until its outcome is recorded one way or
  the other, so a store that is actually back up (or actually down) must never be left
  un-adjudicated.
- **One breaker for the whole port**, matching ``app.reliability.breaker``'s own design principle:
  a Postgres outage fails every method identically, so one shared instance is the only signal
  worth tracking, not six independent ones that would trip in lockstep anyway.
- **The wrapped port's own return shape is preserved exactly**, including the three-way
  ``value | ToolFailure | None`` some methods use: this module never invents a new outcome, only
  decides whether to retry the one the inner port already returned.

Runtime Contract
----------------
``RetriedToolPort(inner, *, policy, breaker, sleep=time.sleep)`` implementing
``contracts.service_v1.tools.ToolPort``.
"""

from __future__ import annotations

# Standard libraries
import logging  # Retry attempts, matching the codebase's own structured-logging convention
import time
from collections.abc import Callable
from typing import TypeVar

# Local modules
from app.domain.policy.models import PolicyDecision
from app.reliability.breaker import CircuitBreaker
from app.reliability.retry import RetryPolicy, backoff_seconds
from contracts.service_v1.cases import CaseRecord
from contracts.service_v1.tools import (
    CreateDisputeCaseRequest,
    CreateDisputeCaseResult,
    EvaluateDisputeRequest,
    Tool,
    ToolFailure,
    ToolPort,
    TransactionFact,
    TransactionFilters,
    TransactionPage,
)

logger = logging.getLogger(__name__)

_R = TypeVar("_R")


class RetriedToolPort:
    """A ``ToolPort`` that retries a retryable ``ToolFailure``, behind a shared circuit breaker."""

    def __init__(
        self,
        inner: ToolPort,
        *,
        policy: RetryPolicy,
        breaker: CircuitBreaker,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._inner = inner
        self._policy = policy
        self._breaker = breaker
        self._sleep = sleep

    def _call(self, tool: Tool, call: Callable[[], _R | ToolFailure]) -> _R | ToolFailure:
        if not self._breaker.allow():
            return ToolFailure(tool=tool, cause="circuit_open", retryable=False)
        attempt = 0
        while True:
            attempt += 1
            try:
                result = call()
            except Exception:
                # The wrapped port raised directly instead of returning a ``ToolFailure`` (for
                # example ``PostgresToolPort``'s fail-closed audit write): still resolve the
                # breaker before propagating, or a half-open trial that hits this could stay
                # wedged just like an unresolved return value would.
                self._breaker.record_failure()
                raise
            retryable_failure = (
                isinstance(result, ToolFailure)
                and result.retryable
                and result.cause != "circuit_open"
            )
            if retryable_failure:
                if attempt >= self._policy.max_attempts:
                    self._breaker.record_failure()
                    return result
                logger.warning(
                    "tool_retry_attempt tool=%s attempt=%s max_attempts=%s",
                    tool.value,
                    attempt,
                    self._policy.max_attempts,
                )
                self._sleep(backoff_seconds(self._policy, attempt))
                continue
            # Any other outcome — a real value, ``None``, or a non-retryable ``ToolFailure`` —
            # means the store was reached and answered, so it resolves the breaker just like a
            # success would, even when the caller sees a business-level refusal.
            self._breaker.record_success()
            return result

    def list_transactions(self, filters: TransactionFilters) -> TransactionPage | ToolFailure:
        return self._call(Tool.LIST_TRANSACTIONS, lambda: self._inner.list_transactions(filters))

    def get_transaction(self, ref: str) -> TransactionFact | ToolFailure | None:
        return self._call(Tool.GET_TRANSACTION, lambda: self._inner.get_transaction(ref))

    def list_dispute_cases(self) -> tuple[CaseRecord, ...] | ToolFailure:
        return self._call(Tool.LIST_DISPUTE_CASES, self._inner.list_dispute_cases)

    def get_case(self, case_number: str) -> CaseRecord | ToolFailure | None:
        return self._call(Tool.GET_CASE, lambda: self._inner.get_case(case_number))

    def evaluate_dispute(
        self, request: EvaluateDisputeRequest
    ) -> PolicyDecision | ToolFailure | None:
        return self._call(Tool.EVALUATE_DISPUTE, lambda: self._inner.evaluate_dispute(request))

    def create_dispute_case(
        self, request: CreateDisputeCaseRequest
    ) -> CreateDisputeCaseResult | ToolFailure:
        return self._call(
            Tool.CREATE_DISPUTE_CASE, lambda: self._inner.create_dispute_case(request)
        )
