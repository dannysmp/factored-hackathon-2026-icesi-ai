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
            result = call()
            if (
                isinstance(result, ToolFailure)
                and result.retryable
                and result.cause != "circuit_open"
            ):
                if attempt >= self._policy.max_attempts:
                    self._breaker.record_failure()
                    return result
                self._sleep(backoff_seconds(self._policy, attempt))
                continue
            if not isinstance(result, ToolFailure):
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

    def evaluate_dispute(self, request: EvaluateDisputeRequest) -> PolicyDecision | ToolFailure:
        return self._call(Tool.EVALUATE_DISPUTE, lambda: self._inner.evaluate_dispute(request))

    def create_dispute_case(
        self, request: CreateDisputeCaseRequest
    ) -> CreateDisputeCaseResult | ToolFailure:
        return self._call(
            Tool.CREATE_DISPUTE_CASE, lambda: self._inner.create_dispute_case(request)
        )
