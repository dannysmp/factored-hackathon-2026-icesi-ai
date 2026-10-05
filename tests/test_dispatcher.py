"""
Tool Dispatcher Tests
======================

Component: ``app.tools.dispatcher``. Hermetic: a hand-written stub records which method was
called and with what, so every branch of the dispatch table is proven, not assumed. The stub
returns plain sentinel strings rather than real result objects (only routing is under test here,
never a result's shape), so it is passed to ``dispatch`` through ``cast``, not by pretending to
satisfy ``ToolPort``'s exact return types.
"""

from __future__ import annotations

from typing import cast

import pytest

from app.tools.dispatcher import dispatch
from contracts.service_v1.tools import Tool, ToolPort


class _RecordingPort:
    """Records the last call made on it; each method returns a distinct sentinel string."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def list_transactions(self, filters: object) -> str:
        self.calls.append(("list_transactions", filters))
        return "page"

    def get_transaction(self, ref: object) -> str:
        self.calls.append(("get_transaction", ref))
        return "fact"

    def list_dispute_cases(self) -> str:
        self.calls.append(("list_dispute_cases", None))
        return "cases"

    def get_case(self, case_number: object) -> str:
        self.calls.append(("get_case", case_number))
        return "case"

    def evaluate_dispute(self, request: object) -> str:
        self.calls.append(("evaluate_dispute", request))
        return "decision"

    def create_dispute_case(self, request: object) -> str:
        self.calls.append(("create_dispute_case", request))
        return "created"


@pytest.mark.parametrize(
    ("tool", "payload", "method", "result"),
    [
        (Tool.LIST_TRANSACTIONS, "filters", "list_transactions", "page"),
        (Tool.GET_TRANSACTION, "TRX-1", "get_transaction", "fact"),
        (Tool.LIST_DISPUTE_CASES, None, "list_dispute_cases", "cases"),
        (Tool.GET_CASE, "CASE-1", "get_case", "case"),
        (Tool.EVALUATE_DISPUTE, "request", "evaluate_dispute", "decision"),
    ],
)
def test_each_tool_calls_its_own_method_with_the_given_payload(
    tool: Tool, payload: object, method: str, result: str
) -> None:
    port = _RecordingPort()

    outcome = dispatch(cast(ToolPort, port), tool, payload)

    assert outcome == result
    assert port.calls == [(method, payload)]


def test_create_dispute_case_is_not_dispatched_here() -> None:
    """The create tool has its own permission invariants; this module does not share them."""
    port = _RecordingPort()

    with pytest.raises(KeyError, match="create_dispute_case"):
        dispatch(cast(ToolPort, port), Tool.CREATE_DISPUTE_CASE, "request")

    assert port.calls == []
