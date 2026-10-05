"""
Tool Failure Injector
======================

Overview
--------
A ``ToolPort`` decorator that fails every call to one named tool, driven by a case's own
``evals.models.InjectedToolFailure``, and passes every other call straight through to the real
port underneath. This is how the harness reproduces "an injected tool failure" without the tool
layer itself knowing anything about the evaluation harness.

Scope
-----
In: ``FailureInjectingToolPort``, wrapping any ``ToolPort`` implementation, and
``FailureSchedule``, the failure the running case declares, read when a system builds its port.
Out: choosing which tool a case fails (``evals.models.InjectedToolFailure``, stated on the case),
running a case end to end (the runner), and the tool layer's own implementation (``app.tools``,
untouched by this module).

Design Principles
-----------------
- **A decorator, not a fork.** Every method delegates to the wrapped port unchanged except the one
  tool a case names; there is exactly one real `ToolPort` implementation, here or in `app`, per
  the rule against duplicate implementations of the same thing.
- **A pure wrapper, and one deliberately mutable schedule.** ``FailureInjectingToolPort`` has no
  side effect of its own: it either forwards to the inner port or returns a `ToolFailure` built
  from the case's own declared cause, and is a frozen dataclass. ``FailureSchedule`` is the one
  piece of state, a single slot a sequential runner sets per case and clears when the batch ends;
  it is owned by the caller, never a module-level instance.
- **Every call to the named tool fails, not just the first.** A case that declares
  `injected_failure` is testing what happens when that tool is down for the whole case, which is
  the condition every tool-failure case in the golden set actually describes.

Runtime Contract
-----------------
``FailureInjectingToolPort(inner, failure)`` implements ``contracts.service_v1.tools.ToolPort``.

``FailureSchedule`` holds the failure the running case declares. A runner sets ``failure`` before
each case; ``FailureSchedule.decorate`` is the ``tool_port_decorator`` a system builds its tool
port through, reading the schedule when each request builds its port, so one application serves a
whole batch and each case gets exactly its own failure (or none).
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass  # Immutable wrapper, no hidden state

# Local modules
from app.domain.policy.models import PolicyDecision  # evaluate_dispute's result, when not failed
from app.security.sessions import Principal  # The decorator hook's per-request argument
from contracts.service_v1.cases import CaseRecord  # list_dispute_cases/get_case result rows
from contracts.service_v1.tools import (  # The port this wraps and its request/result types
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
from evals.models import InjectedToolFailure  # Which tool a case fails, and how


@dataclass(frozen=True, slots=True)
class FailureInjectingToolPort:
    """Wraps ``inner``, failing every call to ``failure.tool`` and forwarding everything else."""

    inner: ToolPort
    failure: InjectedToolFailure | None

    def _failure_for(self, tool: Tool) -> ToolFailure | None:
        """``ToolFailure`` for this call, or ``None`` when it should pass through to ``inner``."""
        if self.failure is not None and self.failure.tool is tool:
            return ToolFailure(
                tool=tool, cause=self.failure.cause, retryable=self.failure.retryable
            )
        return None

    def list_transactions(self, filters: TransactionFilters) -> TransactionPage | ToolFailure:
        failure = self._failure_for(Tool.LIST_TRANSACTIONS)
        return failure if failure is not None else self.inner.list_transactions(filters)

    def get_transaction(self, ref: str) -> TransactionFact | ToolFailure | None:
        failure = self._failure_for(Tool.GET_TRANSACTION)
        return failure if failure is not None else self.inner.get_transaction(ref)

    def list_dispute_cases(self) -> tuple[CaseRecord, ...] | ToolFailure:
        failure = self._failure_for(Tool.LIST_DISPUTE_CASES)
        return failure if failure is not None else self.inner.list_dispute_cases()

    def get_case(self, case_number: str) -> CaseRecord | ToolFailure | None:
        failure = self._failure_for(Tool.GET_CASE)
        return failure if failure is not None else self.inner.get_case(case_number)

    def evaluate_dispute(
        self, request: EvaluateDisputeRequest
    ) -> PolicyDecision | ToolFailure | None:
        failure = self._failure_for(Tool.EVALUATE_DISPUTE)
        return failure if failure is not None else self.inner.evaluate_dispute(request)

    def create_dispute_case(
        self, request: CreateDisputeCaseRequest
    ) -> CreateDisputeCaseResult | ToolFailure:
        failure = self._failure_for(Tool.CREATE_DISPUTE_CASE)
        return failure if failure is not None else self.inner.create_dispute_case(request)


class FailureSchedule:
    """The failure the case now running declares, shared by a runner and one system's tool ports.

    The one mutable object in this module, by design: a runner sets ``failure`` once per case, and
    every tool port built for that case's requests reads it. It lives for one batch, owned by the
    caller that builds the system, never at module level.
    """

    __slots__ = ("failure",)

    def __init__(self) -> None:
        self.failure: InjectedToolFailure | None = None

    def decorate(self, principal: Principal, port: ToolPort) -> ToolPort:
        """Wrap ``port`` with the failure declared right now; ``port`` unchanged when none is."""
        del principal
        if self.failure is None:
            return port
        return FailureInjectingToolPort(port, self.failure)
