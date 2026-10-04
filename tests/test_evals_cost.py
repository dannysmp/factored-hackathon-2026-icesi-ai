"""
Evaluation Cost Accounting Tests
=================================

Component: ``evals.cost``. Hermetic: log records are emitted directly and the wrapped model client
is a stub, so nothing here touches a store or the network.
"""

from __future__ import annotations

# Standard libraries
import logging
from types import SimpleNamespace
from typing import Any, cast

# Local modules
from app.conversation.controller import DialogueController
from app.conversation.understanding import TurnAccounting
from app.llm.client import CompletionRequest, CompletionResult
from app.llm.pricing import cost_usd
from evals.cost import CostTrackingLlm, TurnCostLedger

_CONTROLLER_LOGGER = "app.conversation.controller"
_MODEL = "claude-haiku-4-5-20251001"


def _turn_line(session_id: str, cost: str) -> str:
    return (
        f"turn_completed session_id={session_id} case_number=None model={_MODEL} "
        f"prompt_version=nlu_v1 input_tokens=10 output_tokens=5 latency_ms=1.0 cost_usd={cost}"
    )


def _emit(message: str) -> None:
    logging.getLogger(_CONTROLLER_LOGGER).info(message)


def test_the_ledger_sums_a_sessions_turns_and_keeps_sessions_apart() -> None:
    with TurnCostLedger() as ledger:
        _emit(_turn_line("S-1", "0.0010"))
        _emit(_turn_line("S-2", "0.0200"))
        _emit(_turn_line("S-1", "0.0025"))

    assert ledger.cost_for("S-1") == 0.0035
    assert ledger.cost_for("S-2") == 0.02


def test_a_session_with_no_logged_turn_has_no_cost_rather_than_zero() -> None:
    with TurnCostLedger() as ledger:
        _emit(_turn_line("S-1", "0.0010"))

    assert ledger.cost_for("S-unknown") is None


def test_an_unpriced_turn_makes_the_whole_session_cost_unknown() -> None:
    with TurnCostLedger() as ledger:
        _emit(_turn_line("S-1", "0.0010"))
        _emit(_turn_line("S-1", "None"))
        _emit(_turn_line("S-1", "0.0010"))

    assert ledger.cost_for("S-1") is None


def test_a_turn_with_no_model_call_counts_as_a_measured_zero() -> None:
    with TurnCostLedger() as ledger:
        _emit(_turn_line("S-1", "0"))

    assert ledger.cost_for("S-1") == 0.0


def test_lines_that_are_not_turn_completed_are_ignored() -> None:
    with TurnCostLedger() as ledger:
        _emit("model_render_call model=x intent=y session_id=S-1 cost_usd=5")
        _emit("something else entirely")

    assert ledger.cost_for("S-1") is None


def test_the_ledger_restores_the_logger_it_attached_to() -> None:
    logger = logging.getLogger(_CONTROLLER_LOGGER)
    level_before = logger.level
    handlers_before = list(logger.handlers)

    with TurnCostLedger() as ledger:
        assert ledger in logger.handlers

    assert logger.level == level_before
    assert logger.handlers == handlers_before
    _emit(_turn_line("S-late", "9"))
    assert ledger.cost_for("S-late") is None


class _StubLlm:
    """Serves each call with the next model in ``models``."""

    def __init__(self, *models: str) -> None:
        self._models = list(models)

    def complete(self, request: CompletionRequest) -> CompletionResult:
        return CompletionResult(
            tool_input={},
            model=self._models.pop(0),
            prompt_version="1",
            input_tokens=1000,
            output_tokens=500,
            latency_ms=1.0,
        )


def _request() -> Any:
    return object()


def test_the_tracking_client_sums_priced_calls_and_counts_them() -> None:
    client = CostTrackingLlm(_StubLlm(_MODEL, _MODEL))
    one_call = CostTrackingLlm(_StubLlm(_MODEL))

    client.complete(_request())
    client.complete(_request())
    one_call.complete(_request())

    assert client.call_count == 2
    assert one_call.total_cost_usd is not None and one_call.total_cost_usd > 0
    assert client.total_cost_usd == 2 * one_call.total_cost_usd


def test_one_unpriced_call_makes_the_total_unknown_for_good() -> None:
    client = CostTrackingLlm(_StubLlm(_MODEL, "claude-opus-4", _MODEL))

    for _ in range(3):
        client.complete(_request())

    assert client.call_count == 3
    assert client.total_cost_usd is None


def _log_through_the_controller(session_id: str, accounting: TurnAccounting | None) -> None:
    state = SimpleNamespace(session_id=session_id, last_case_number=None)
    method = cast(Any, DialogueController._log_turn_completed)
    method(SimpleNamespace(), state, accounting)


def test_the_ledger_reads_the_line_the_controller_really_writes() -> None:
    priced = TurnAccounting(
        model="claude-haiku-4-5-20251001",
        prompt_version="1",
        input_tokens=1000,
        output_tokens=100,
        latency_ms=12.0,
    )
    unpriced = TurnAccounting(
        model="a-model-with-no-price",
        prompt_version="1",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1.0,
    )

    with TurnCostLedger() as ledger:
        _log_through_the_controller("sess-priced", priced)
        _log_through_the_controller("sess-priced", priced)
        _log_through_the_controller("sess-keyword", None)
        _log_through_the_controller("sess-unpriced", unpriced)

    one_call = cost_usd(priced.model, priced.input_tokens, priced.output_tokens)
    assert ledger.cost_for("sess-priced") == float(one_call * 2)
    assert (ledger.cost_for("sess-priced") or 0) > 0
    assert ledger.cost_for("sess-keyword") == 0.0
    assert ledger.cost_for("sess-unpriced") is None
