"""
Model Renderer Tests
=====================

Component: ``app.conversation.model_renderer.LlmRenderer``. Hermetic: ``FakeLlm``, no network.
"""

from __future__ import annotations

# Standard libraries
from datetime import date
from decimal import Decimal

# Local modules
from app.conversation.model_renderer import LlmRenderer
from app.domain.policy.models import TransactionStatus
from app.llm.client import FakeLlm, LlmRequestRejected
from contracts.service_v1.envelope import (
    DisputeFacts,
    Intent,
    Money,
    ProductLabel,
    RenderEnvelope,
    TransactionFact,
)

_DOMAIN_DATE = date(2026, 6, 18)


def _envelope() -> RenderEnvelope:
    fact = TransactionFact(
        ref="tx-1",
        occurred_on=date(2026, 6, 1),
        merchant="Tienda",
        amount=Money(amount=Decimal("10.00"), currency="MXN"),
        product=ProductLabel(name="Visa", last4="1234"),
        status=TransactionStatus.APPROVED,
    )
    return RenderEnvelope(
        session_id="s1",
        lang="es",
        domain_date=_DOMAIN_DATE,
        intent=Intent.PRESENT_TRANSACTIONS,
        facts=DisputeFacts(transactions=(fact,), candidate_count=1),
        render_mode="model",
    )


def test_render_returns_none_when_the_llm_call_fails() -> None:
    llm = FakeLlm(responses=[LlmRequestRejected("no access")])
    renderer = LlmRenderer(llm, model="claude-sonnet-5")

    assert renderer.render(_envelope()) is None


def test_render_returns_none_when_the_tool_did_not_return_text() -> None:
    llm = FakeLlm(responses=[{"not_text": "oops"}])
    renderer = LlmRenderer(llm, model="claude-sonnet-5")

    assert renderer.render(_envelope()) is None


def test_render_repairs_an_overlong_candidate_by_truncating_once() -> None:
    overlong = "a" * 2500
    llm = FakeLlm(responses=[{"text": overlong}])
    renderer = LlmRenderer(llm, model="claude-sonnet-5")

    result = renderer.render(_envelope())

    assert result is not None
    assert len(result.raw_text) == 2000


def test_render_returns_none_when_even_truncation_leaves_it_invalid() -> None:
    """An empty string is invalid before and after truncation: the repair cannot save it."""
    llm = FakeLlm(responses=[{"text": ""}])
    renderer = LlmRenderer(llm, model="claude-sonnet-5")

    assert renderer.render(_envelope()) is None


def test_render_sends_only_the_intent_and_field_names_never_a_grounded_value() -> None:
    llm = FakeLlm(responses=[{"text": "{{amount}}"}])
    renderer = LlmRenderer(llm, model="claude-sonnet-5")

    renderer.render(_envelope())

    sent = llm.requests[0].user_text
    assert "10.00" not in sent
    assert "Tienda" not in sent
    assert "{{amount}}" in sent
    assert "{{merchant}}" in sent
