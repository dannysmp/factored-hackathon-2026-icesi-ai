"""
Evaluation Cost Accounting
===========================

Overview
--------
Turns the model spend a run already leaves in its own structured logs, and the spend of the
judge's own calls, into the per-case figures the evaluation report states. A run's cost is read
back the way an operator would read it, from the stable ``turn_completed`` log line
(``app.conversation.controller``), so the harness never reaches past the HTTP surface into the
process to learn what a case cost.

Scope
-----
In: ``TurnCostLedger`` (per-session cost of the controller's own ``turn_completed`` lines) and
``CostTrackingLlm`` (the spend of every call made through a wrapped ``LlmClient``).
Out: pricing (``app.llm.pricing``), B1's own call pricing (``evals.runner.baselines.b1`` prices
its calls where it makes them), and rendering (``evals.report``).

Design Principles
-----------------
- **Unknown is never zero.** A session with no ``turn_completed`` line, or any line whose cost
  is ``None`` (an unpriced model), has no cost: ``cost_for`` returns ``None`` and the metric
  layer excludes the case from its cost denominator instead of averaging in an invented figure.
- **The judge is evaluation tooling, not a system.** ``CostTrackingLlm`` is the only place the
  judge's spend is measured and is reported on its own line; it never enters any system's cost.
- **Money stays ``Decimal`` until it becomes a report figure.**

Runtime Contract
----------------
``with TurnCostLedger() as ledger:`` captures every ``turn_completed`` line emitted in the block;
``ledger.cost_for(session_id) -> float | None``. ``CostTrackingLlm(inner)`` implements
``LlmClient``; ``total_cost_usd`` is ``None`` once any call went to an unpriced model.

Limitations
-----------
The ledger sees only log lines emitted inside this process, which is where every harness run
drives the application (an in-process ASGI client); it would read nothing from a deployed instance.
Only understanding calls are priced by the controller's ``turn_completed`` line; model-rendered
replies (``MODEL_RENDERER_ENABLED``, off by default) log no cost and are not counted.
"""

from __future__ import annotations

# Standard libraries
import logging
import re
import threading
from decimal import Decimal, InvalidOperation
from types import TracebackType

# Local modules
from app.llm.client import CompletionRequest, CompletionResult, LlmClient  # The port this wraps
from app.llm.pricing import cost_usd  # Tokens to dollars

_CONTROLLER_LOGGER = "app.conversation.controller"
_TURN_COMPLETED = re.compile(
    r"^turn_completed session_id=(?P<session>\S+) .* cost_usd=(?P<cost>\S+)$"
)

logger = logging.getLogger(__name__)


class TurnCostLedger(logging.Handler):
    """Per-session model cost, from the controller's own ``turn_completed`` log lines."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self._lock = threading.Lock()
        self._costs: dict[str, Decimal | None] = {}
        self._previous_level: int | None = None

    def emit(self, record: logging.LogRecord) -> None:
        match = _TURN_COMPLETED.match(record.getMessage())
        if match is None:
            return
        try:
            cost: Decimal | None = Decimal(match["cost"])
        except InvalidOperation:
            cost = None
        session = match["session"]
        with self._lock:
            known = self._costs.get(session, Decimal(0))
            self._costs[session] = None if cost is None or known is None else known + cost

    def __enter__(self) -> TurnCostLedger:
        controller_logger = logging.getLogger(_CONTROLLER_LOGGER)
        self._previous_level = controller_logger.level
        controller_logger.setLevel(logging.INFO)
        controller_logger.addHandler(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        controller_logger = logging.getLogger(_CONTROLLER_LOGGER)
        controller_logger.removeHandler(self)
        if self._previous_level is not None:
            controller_logger.setLevel(self._previous_level)

    def cost_for(self, session_id: str) -> float | None:
        """The session's total cost in USD, or ``None`` when it is not known."""
        with self._lock:
            cost = self._costs.get(session_id)
        return None if cost is None else float(cost)


class CostTrackingLlm(LlmClient):
    """Wraps a client and sums the priced cost of every call made through it."""

    def __init__(self, inner: LlmClient) -> None:
        self._inner = inner
        self.call_count = 0
        self._total: Decimal | None = Decimal(0)

    @property
    def total_cost_usd(self) -> float | None:
        """Spend across every call so far; ``None`` once any call went to an unpriced model."""
        return None if self._total is None else float(self._total)

    def complete(self, request: CompletionRequest) -> CompletionResult:
        result = self._inner.complete(request)
        self.call_count += 1
        try:
            call_cost = cost_usd(result.model, result.input_tokens, result.output_tokens)
        except KeyError:
            logger.warning("judge_call_cost_unpriced model=%s", result.model)
            self._total = None
        else:
            if self._total is not None:
                self._total += call_cost
        return result
