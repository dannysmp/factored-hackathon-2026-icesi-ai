"""
Tool Failure Injector Tests
============================

Component: ``evals.injector``. Hermetic: a hand-written stub records which method was called and
returns plain sentinel strings (only routing and the failure/passthrough choice are under test
here, never a result's shape), so both the stub and its sentinel results are moved across
``FailureInjectingToolPort``'s real, precisely-typed signature through ``cast``, matching
``tests.test_dispatcher``'s own convention for the same kind of stub.
"""

from __future__ import annotations

# Standard libraries
from typing import cast

# Local modules
from contracts.service_v1.tools import (
    CreateDisputeCaseRequest,
    EvaluateDisputeRequest,
    Tool,
    ToolFailure,
    ToolPort,
    TransactionFilters,
)
from evals.injector import FailureInjectingToolPort
from evals.models import InjectedToolFailure


class _RecordingPort:
    """Records every call made on it; each method returns a distinct sentinel string."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_transactions(self, filters: object) -> str:
        self.calls.append("list_transactions")
        return "page"

    def get_transaction(self, ref: object) -> str:
        self.calls.append("get_transaction")
        return "transaction"

    def list_dispute_cases(self) -> str:
        self.calls.append("list_dispute_cases")
        return "cases"

    def get_case(self, case_number: object) -> str:
        self.calls.append("get_case")
        return "case"

    def evaluate_dispute(self, request: object) -> str:
        self.calls.append("evaluate_dispute")
        return "decision"

    def create_dispute_case(self, request: object) -> str:
        self.calls.append("create_dispute_case")
        return "created"


def _wrap(inner: _RecordingPort, failure: InjectedToolFailure | None) -> FailureInjectingToolPort:
    return FailureInjectingToolPort(inner=cast(ToolPort, inner), failure=failure)


def test_no_injected_failure_forwards_every_call_unchanged() -> None:
    inner = _RecordingPort()
    port = _wrap(inner, failure=None)
    filters = cast(TransactionFilters, "filters")
    evaluate_request = cast(EvaluateDisputeRequest, "request")
    create_request = cast(CreateDisputeCaseRequest, "request")

    assert cast(object, port.list_transactions(filters)) == "page"
    assert cast(object, port.get_transaction("tx-1")) == "transaction"
    assert cast(object, port.list_dispute_cases()) == "cases"
    assert cast(object, port.get_case("D-1")) == "case"
    assert cast(object, port.evaluate_dispute(evaluate_request)) == "decision"
    assert cast(object, port.create_dispute_case(create_request)) == "created"
    assert inner.calls == [
        "list_transactions",
        "get_transaction",
        "list_dispute_cases",
        "get_case",
        "evaluate_dispute",
        "create_dispute_case",
    ]


def test_the_named_tool_fails_every_time_it_is_called() -> None:
    inner = _RecordingPort()
    failure = InjectedToolFailure(tool=Tool.LIST_TRANSACTIONS, cause="timeout")
    port = _wrap(inner, failure)
    filters = cast(TransactionFilters, "filters")

    first = port.list_transactions(filters)
    second = port.list_transactions(filters)

    assert first == ToolFailure(tool=Tool.LIST_TRANSACTIONS, cause="timeout", retryable=True)
    assert second == first
    assert inner.calls == []


def test_a_call_to_a_different_tool_still_passes_through() -> None:
    inner = _RecordingPort()
    failure = InjectedToolFailure(tool=Tool.CREATE_DISPUTE_CASE, cause="error")
    port = _wrap(inner, failure)
    filters = cast(TransactionFilters, "filters")
    evaluate_request = cast(EvaluateDisputeRequest, "request")

    assert cast(object, port.list_transactions(filters)) == "page"
    assert cast(object, port.get_transaction("tx-1")) == "transaction"
    assert cast(object, port.list_dispute_cases()) == "cases"
    assert cast(object, port.get_case("D-1")) == "case"
    assert cast(object, port.evaluate_dispute(evaluate_request)) == "decision"
    assert inner.calls == [
        "list_transactions",
        "get_transaction",
        "list_dispute_cases",
        "get_case",
        "evaluate_dispute",
    ]


def test_the_injected_failure_carries_its_own_cause_and_retryability() -> None:
    inner = _RecordingPort()
    failure = InjectedToolFailure(tool=Tool.EVALUATE_DISPUTE, cause="circuit_open", retryable=False)
    port = _wrap(inner, failure)
    evaluate_request = cast(EvaluateDisputeRequest, "request")

    result = port.evaluate_dispute(evaluate_request)

    assert result == ToolFailure(tool=Tool.EVALUATE_DISPUTE, cause="circuit_open", retryable=False)
