"""
Daily Model Spend Breaker Tests
================================

Component: ``app.llm.spend``. A tripped breaker surfaces as the provider being unavailable, which
the dialogue controller already answers with a handoff (understanding) or a template reply
(rendering); those degradations are covered in ``test_dialogue_controller.py`` and
``test_model_renderer.py``. Hermetic: an in-memory ledger and a priced stub client, so every path
(under the limit, at the limit, an unreadable total, the day rollover) runs without a database.
"""

from __future__ import annotations

# Standard libraries
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

# Third-party libraries
import pytest

# Local modules
from app.conversation.llm_understanding import LlmNlu
from app.conversation.model_renderer import LlmRenderer, RenderUnavailable
from app.conversation.understanding import UnderstandingUnavailable
from app.llm.client import CompletionRequest, CompletionResult, LlmUnavailable, ToolSpec
from app.llm.spend import LlmSpendLimitReached, SpendGatedLlmClient
from tests.test_model_renderer import _envelope

_MODEL = "claude-sonnet-5"
_TOOL = ToolSpec(name="t", description="d", input_schema={})
_REQUEST = CompletionRequest(
    model=_MODEL, system="s", user_text="u", tool=_TOOL, prompt_version="1"
)
_NOON_UTC = datetime(2026, 6, 18, 17, 0, tzinfo=UTC)


@dataclass
class _Ledger:
    totals: dict[date, Decimal] = field(default_factory=dict)
    fail_reads: bool = False
    fail_writes: bool = False

    def spent(self, day: date) -> Decimal:
        if self.fail_reads:
            raise RuntimeError("database unreachable")
        return self.totals.get(day, Decimal(0))

    def add(self, day: date, usd: Decimal) -> None:
        if self.fail_writes:
            raise RuntimeError("database unreachable")
        self.totals[day] = self.totals.get(day, Decimal(0)) + usd


@dataclass
class _PricedLlm:
    """Answers every call with a fixed tool input and a fixed, priced token count."""

    input_tokens: int = 1_000_000
    output_tokens: int = 0
    calls: int = 0

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            tool_input={"text": "hola"},
            model=request.model,
            prompt_version=request.prompt_version,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            latency_ms=1.0,
        )


def _gate(
    inner: _PricedLlm,
    ledger: _Ledger,
    *,
    limit: str = "10",
    now: datetime = _NOON_UTC,
) -> SpendGatedLlmClient:
    return SpendGatedLlmClient(inner, ledger, Decimal(limit), lambda: now)


def test_a_call_under_the_limit_passes_through_and_is_charged() -> None:
    inner, ledger = _PricedLlm(), _Ledger()

    result = _gate(inner, ledger).complete(_REQUEST)

    assert result.tool_input == {"text": "hola"}
    assert ledger.totals == {date(2026, 6, 18): Decimal("2")}


def test_a_call_at_the_limit_is_refused_without_reaching_the_model() -> None:
    inner = _PricedLlm()
    ledger = _Ledger(totals={date(2026, 6, 18): Decimal("10")})

    with pytest.raises(LlmSpendLimitReached):
        _gate(inner, ledger).complete(_REQUEST)

    assert inner.calls == 0


def test_the_call_that_crosses_the_limit_completes_and_the_next_is_refused() -> None:
    inner = _PricedLlm(input_tokens=3_000_000)
    ledger = _Ledger(totals={date(2026, 6, 18): Decimal("9")})
    gate = _gate(inner, ledger)

    gate.complete(_REQUEST)

    assert ledger.totals[date(2026, 6, 18)] == Decimal("15")
    with pytest.raises(LlmSpendLimitReached):
        gate.complete(_REQUEST)
    assert inner.calls == 1


def test_an_unreadable_total_refuses_the_call_and_logs_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inner, ledger = _PricedLlm(), _Ledger(fail_reads=True)

    with caplog.at_level(logging.ERROR), pytest.raises(LlmSpendLimitReached):
        _gate(inner, ledger).complete(_REQUEST)

    assert inner.calls == 0
    assert "daily_spend_unreadable" in caplog.text


def test_a_failed_charge_never_blocks_the_reply_the_call_produced(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inner, ledger = _PricedLlm(), _Ledger(fail_writes=True)

    with caplog.at_level(logging.ERROR):
        result = _gate(inner, ledger).complete(_REQUEST)

    assert result.tool_input == {"text": "hola"}
    assert "daily_spend_charge_failed" in caplog.text


def test_the_day_is_the_bank_operating_day_not_the_utc_day() -> None:
    inner, ledger = _PricedLlm(), _Ledger()
    # 04:30 UTC on 19 June is 23:30 on 18 June in Bogota (UTC-5).
    late_evening = datetime(2026, 6, 19, 4, 30, tzinfo=UTC)

    _gate(inner, ledger, now=late_evening).complete(_REQUEST)

    assert list(ledger.totals) == [date(2026, 6, 18)]


def test_the_limit_resets_when_the_operating_day_rolls_over() -> None:
    inner = _PricedLlm()
    ledger = _Ledger(totals={date(2026, 6, 18): Decimal("10")})
    # 05:00 UTC on 19 June is 00:00 on 19 June in Bogota.
    after_midnight = datetime(2026, 6, 19, 5, 0, tzinfo=UTC)

    _gate(inner, ledger, now=after_midnight).complete(_REQUEST)

    assert inner.calls == 1


def test_a_tripped_breaker_is_an_unavailable_provider_to_every_caller() -> None:
    assert issubclass(LlmSpendLimitReached, LlmUnavailable)


def test_a_tripped_breaker_makes_understanding_unavailable() -> None:
    ledger = _Ledger(totals={date(2026, 6, 18): Decimal("10")})
    nlu = LlmNlu(_gate(_PricedLlm(), ledger), model="claude-haiku-4-5-20251001")

    with pytest.raises(UnderstandingUnavailable):
        nlu.understand(
            "quiero disputar un cargo", language_hint="es", reference_date=date(2026, 6, 18)
        )


def test_a_tripped_breaker_makes_the_model_renderer_unavailable() -> None:
    ledger = _Ledger(totals={date(2026, 6, 18): Decimal("10")})
    renderer = LlmRenderer(_gate(_PricedLlm(), ledger), model=_MODEL)

    with pytest.raises(RenderUnavailable):
        renderer.render(_envelope())
