"""
Reply Orchestrator Tests
=========================

Component: ``app.conversation.reply``. Hermetic: ``FakeLlm``, no network.
"""

from __future__ import annotations

# Standard libraries
import logging
from datetime import date
from decimal import Decimal

# Third-party libraries
import pytest

# Local modules
from app.conversation.model_renderer import LlmRenderer
from app.conversation.reply import MODEL_ELIGIBLE_TEMPLATES, render_reply
from app.domain.policy.models import DisputeCategory, Outcome, TransactionStatus
from app.llm.client import FakeLlm, LlmUnavailable
from contracts.service_v1.envelope import (
    CaseFact,
    CustomerReason,
    Decision,
    DisputeFacts,
    Intent,
    Money,
    ProductLabel,
    RenderEnvelope,
    TemplateId,
    TransactionFact,
)

_DOMAIN_DATE = date(2026, 6, 18)


def test_model_eligible_templates_excludes_every_safety_relevant_or_out_of_scope_variant() -> None:
    """Value-level, not derived from which templates the scripted flows happen to exercise: a
    template added back to this set by mistake must fail this test, not just lose test coverage."""
    excluded = {
        TemplateId.NO_CASE_FOUND,
        TemplateId.HANDOFF_CARD_LOSS,
        TemplateId.HANDOFF_REQUESTED,
        TemplateId.HANDOFF_NOT_REGISTERED,
        TemplateId.FILING_UNVERIFIED,
        TemplateId.REFUSE_UNSUPPORTED,
        TemplateId.REFUSE_REVERSAL,
        TemplateId.ABSTAIN_POLICY,
    }
    assert excluded.isdisjoint(MODEL_ELIGIBLE_TEMPLATES)


def _confirm_filing_envelope() -> RenderEnvelope:
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
        intent=Intent.CONFIRM_FILING,
        template_id=TemplateId.CONFIRM_FILING,
        render_mode="template",
        facts=DisputeFacts(
            transactions=(fact,),
            candidate_count=1,
            selected_ref="tx-1",
            category=DisputeCategory.FRAUD_CLAIM,
        ),
        decisions=(
            Decision(
                outcome=Outcome.ELIGIBLE,
                customer_reason=CustomerReason.ELIGIBLE,
                policy_version="2",
                requires_confirmation=True,
            ),
        ),
    )


def test_a_rejected_candidate_logs_its_reasons(caplog: pytest.LogCaptureFixture) -> None:
    llm = FakeLlm(responses=[{"text": "El monto es 100."}])
    renderer = LlmRenderer(llm, model="claude-sonnet-5")

    with caplog.at_level(logging.INFO):
        render_reply(_confirm_filing_envelope(), model_renderer=renderer)

    messages = [r.getMessage() for r in caplog.records]
    rejected = [m for m in messages if "render_reply_rejected" in m]
    assert rejected
    assert "digit_outside_slot" in rejected[0]


def test_a_failed_model_call_logs_the_fallback(caplog: pytest.LogCaptureFixture) -> None:
    llm = FakeLlm(responses=[LlmUnavailable("boom")])
    renderer = LlmRenderer(llm, model="claude-sonnet-5")

    with caplog.at_level(logging.INFO):
        render_reply(_confirm_filing_envelope(), model_renderer=renderer)

    messages = [r.getMessage() for r in caplog.records]
    assert any("model_render_call_failed" in m and "claude-sonnet-5" in m for m in messages)
    assert any("render_reply_fallback" in m for m in messages)


def test_an_accepted_candidate_logs_acceptance(caplog: pytest.LogCaptureFixture) -> None:
    llm = FakeLlm(responses=[{"text": "{{amount}} {{occurred_on}} {{category}}"}])
    renderer = LlmRenderer(llm, model="claude-sonnet-5")

    with caplog.at_level(logging.INFO):
        render_reply(_confirm_filing_envelope(), model_renderer=renderer)

    messages = [r.getMessage() for r in caplog.records]
    assert any("render_reply_accepted" in m for m in messages)
    assert any("model_render_call" in m and "outcome=parsed" in m for m in messages)


def _dispute_status_envelope() -> RenderEnvelope:
    case = CaseFact(
        case_number="D-1", status="Open", filed_on=date(2026, 6, 1), transaction_ref="tx-1"
    )
    return RenderEnvelope(
        session_id="s1",
        lang="es",
        domain_date=_DOMAIN_DATE,
        intent=Intent.DISPUTE_STATUS,
        template_id=TemplateId.DISPUTE_STATUS,
        render_mode="template",
        facts=DisputeFacts(cases=(case,)),
    )


def test_a_dispute_status_reply_with_a_case_still_renders_from_the_model() -> None:
    """The required-field floor added for the no-case state must not regress the case-exists one:
    a model reply citing the case fields plus the outcome statement is still accepted, not
    silently downgraded to the template fallback."""
    llm = FakeLlm(
        responses=[
            {"text": "{{outcome_statement}} Caso {{case_number}}: {{case_status}}, {{filed_on}}."}
        ]
    )
    renderer = LlmRenderer(llm, model="claude-sonnet-5")

    result = render_reply(_dispute_status_envelope(), model_renderer=renderer)

    assert result.render_mode == "model"
