"""
Service Contract Tests
======================

Component: ``contracts.service_v1``. Hermetic and pure: the contracts are declarative models, so
the tests check what they accept, what they refuse and what they cannot carry.

The rule under test throughout: a value a reply, a customer token or a log line must never hold
is not a field of the type that reaches them.
"""

from __future__ import annotations

# Standard libraries
import json  # Fixture files and schema inspection
from collections.abc import Iterator  # Walk of a JSON schema
from datetime import UTC, date, datetime, timedelta, timezone  # Dates and instants
from decimal import Decimal  # Money
from pathlib import Path  # Fixture location
from typing import Any  # Loosely typed JSON

# Third-party libraries
import pytest  # Test runner and fixtures
from pydantic import ValidationError  # Boundary failures

from app.domain.policy.models import (
    DisputeCategory,
    Outcome,
    ReasonCode,
    TransactionStatus,
)

# Local modules
from contracts import service_v1
from contracts.service_v1.api import (
    MAX_TEXT_LENGTH,
    ReadinessPayload,
    ReferenceDateOrigin,
    TurnRequest,
    TurnResponse,
)
from contracts.service_v1.console import (
    QueueItem,
    TicketDetail,
    TicketStatus,
    TimelineEntry,
    is_priority,
)
from contracts.service_v1.envelope import (
    AgentDecision,
    AgentOnly,
    CaseFact,
    CustomerReason,
    Decision,
    DisputeFacts,
    Envelope,
    Intent,
    Lang,
    LocalizedTitle,
    Money,
    ProductLabel,
    RenderEnvelope,
    RiskEvidence,
    Slot,
    SourceRef,
    TemplateId,
    ToolUnavailable,
    TransactionFact,
)
from contracts.service_v1.handoff import (
    ActionRecord,
    CustomerLabel,
    Evidence,
    HandoffPacket,
    HandoffTrigger,
    OpenQuestion,
)
from contracts.service_v1.nlu import NluIntent, NluResult, TransactionHint
from tests.fixtures import scripted_flows

_FIXTURES = Path(__file__).parent / "fixtures"
_DOMAIN_DATE = date(2026, 6, 18)

# Names that carry agent-only or raw material; none may exist in a customer-reachable schema.
_AGENT_ONLY_NAMES = {"reason_code", "triggers", "inputs", "nlu_confidence", "risk", "agent_only"}
_RAW_MATERIAL_NAMES = {"transcript", "message", "document_number", "card_number", "customer_id"}


# -----------------------------------------------------------------------------
# Builders
# -----------------------------------------------------------------------------


def _transaction(**changes: Any) -> TransactionFact:
    values: dict[str, Any] = {
        "ref": "tx-1001",
        "occurred_on": date(2026, 6, 12),
        "merchant": "Tienda Sol",
        "amount": Money(amount=Decimal("250.00"), currency="MXN"),
        "product": ProductLabel(name="Visa Classic", last4="4321"),
        "status": TransactionStatus.APPROVED,
    }
    return TransactionFact(**{**values, **changes})


def _titles() -> tuple[LocalizedTitle, ...]:
    return (
        LocalizedTitle(lang="es", text="Plazos para disputar"),
        LocalizedTitle(lang="pt", text="Prazos para contestar"),
        LocalizedTitle(lang="en", text="Filing windows"),
    )


def _source() -> SourceRef:
    return SourceRef(section_id="filing-windows", titles=_titles(), corpus_version="2")


def _eligible_decision() -> Decision:
    return Decision(
        outcome=Outcome.ELIGIBLE,
        customer_reason=CustomerReason.ELIGIBLE,
        policy_version="2",
        requires_confirmation=True,
    )


def _envelope(**changes: Any) -> Envelope:
    values: dict[str, Any] = {
        "session_id": "s-1",
        "lang": "es",
        "domain_date": _DOMAIN_DATE,
        "intent": Intent.CLARIFY,
        "next_expected": Slot.TRANSACTION,
        "template_id": TemplateId.CLARIFY_TRANSACTION,
    }
    return Envelope(**{**values, **changes})


def _packet(**changes: Any) -> HandoffPacket:
    values: dict[str, Any] = {
        "ticket_ref": "T-100",
        "reference_date": _DOMAIN_DATE,
        "created_at": datetime(2026, 9, 26, 15, 0, tzinfo=UTC),
        "language": "pt",
        "needs_language_routing": True,
        "trigger": HandoffTrigger.FRAUD_REPORT,
        "customer": CustomerLabel(first_name="Ana", masked_id="****1234"),
        "category": DisputeCategory.FRAUD_CLAIM,
        "request_summary": "Reports a card used without authorization.",
        "evidence": Evidence(reason_codes=(ReasonCode.ESCALATE_FRAUD_CLAIM,), policy_version="2"),
    }
    return HandoffPacket(**{**values, **changes})


def _queue_item(**changes: Any) -> QueueItem:
    values: dict[str, Any] = {
        "ticket_ref": "T-100",
        "trigger": HandoffTrigger.FRAUD_REPORT,
        "language": "pt",
        "category": DisputeCategory.FRAUD_CLAIM,
        "status": TicketStatus.OPEN,
        "created_at": datetime(2026, 9, 26, 15, 0, tzinfo=UTC),
        "reference_date": _DOMAIN_DATE,
        "promised_contact_by": date(2026, 6, 19),
        "age_days": 0,
        "priority": True,
    }
    return QueueItem(**{**values, **changes})


def _field_names(schema: Any) -> Iterator[str]:
    """Every property name anywhere in a JSON schema."""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                yield from value
            yield from _field_names(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from _field_names(item)


# -----------------------------------------------------------------------------
# Package
# -----------------------------------------------------------------------------


def test_the_package_init_is_empty() -> None:
    """Neither stream edits the other's files through the package: its init holds nothing."""
    assert Path(service_v1.__file__ or "").read_text() == ""


# -----------------------------------------------------------------------------
# Envelope
# -----------------------------------------------------------------------------


def test_a_minimal_envelope_is_valid_and_carries_the_version() -> None:
    """The smallest legal envelope validates and names the contract version."""
    envelope = _envelope()

    assert envelope.contract_version == "1"
    assert envelope.render_mode == "template"


def test_unknown_fields_are_rejected() -> None:
    """A misspelled or smuggled field fails at the boundary."""
    with pytest.raises(ValidationError):
        _envelope(customer_id="c-9")


def test_an_envelope_is_immutable() -> None:
    """A built envelope cannot be edited."""
    envelope = _envelope()

    with pytest.raises(ValidationError):
        envelope.lang = "en"  # type: ignore[misc]


def test_a_language_outside_the_three_is_rejected() -> None:
    """Only Spanish, Portuguese and English exist."""
    with pytest.raises(ValidationError):
        _envelope(lang="fr")


def test_template_mode_requires_a_template_and_model_mode_forbids_one() -> None:
    """The wording is named exactly when it is fixed."""
    with pytest.raises(ValidationError, match="template mode requires"):
        _envelope(template_id=None)
    with pytest.raises(ValidationError, match="model mode must not"):
        _envelope(render_mode="model")

    assert _envelope(render_mode="model", template_id=None).template_id is None


def test_present_transactions_needs_a_transaction() -> None:
    """An intent that lists transactions holds at least one."""
    facts = DisputeFacts(transactions=(_transaction(),), candidate_count=1)

    assert _envelope(
        intent=Intent.PRESENT_TRANSACTIONS,
        facts=facts,
        template_id=TemplateId.PRESENT_ONE,
    )
    with pytest.raises(ValidationError, match="at least one transaction"):
        _envelope(intent=Intent.PRESENT_TRANSACTIONS, template_id=TemplateId.PRESENT_ONE)


def test_confirm_filing_needs_an_eligible_confirmable_decision_and_a_selection() -> None:
    """A confirmation prompt exists only for a decision that requires one, on a chosen item."""
    facts = DisputeFacts(
        transactions=(_transaction(),),
        candidate_count=1,
        selected_ref="tx-1001",
        category=DisputeCategory.UNRECOGNIZED_CHARGE,
    )
    common: dict[str, Any] = {
        "intent": Intent.CONFIRM_FILING,
        "template_id": TemplateId.CONFIRM_FILING,
        "next_expected": Slot.CONFIRMATION,
    }

    assert _envelope(facts=facts, decisions=(_eligible_decision(),), **common)
    with pytest.raises(ValidationError, match="confirm_filing requires"):
        _envelope(facts=facts, decisions=(), **common)
    with pytest.raises(ValidationError, match="confirm_filing requires"):
        _envelope(decisions=(_eligible_decision(),), **common)


def test_filing_result_needs_the_case_read_back() -> None:
    """A filing is reported only with the case the read-back returned."""
    case = CaseFact(
        case_number="D-1", status="Open", filed_on=_DOMAIN_DATE, transaction_ref="tx-1001"
    )
    common: dict[str, Any] = {
        "intent": Intent.FILING_RESULT,
        "template_id": TemplateId.FILING_RESULT,
    }

    assert _envelope(facts=DisputeFacts(cases=(case,)), **common)
    with pytest.raises(ValidationError, match="case read back"):
        _envelope(**common)


def test_policy_answer_needs_a_source_and_abstention_carries_none() -> None:
    """A policy answer is grounded; an abstention states no source and no figure."""
    answer: dict[str, Any] = {
        "intent": Intent.POLICY_ANSWER,
        "template_id": TemplateId.POLICY_ANSWER,
    }
    abstain: dict[str, Any] = {"intent": Intent.ABSTAIN, "template_id": TemplateId.ABSTAIN_POLICY}

    assert _envelope(sources=(_source(),), **answer)
    with pytest.raises(ValidationError, match="requires at least one source"):
        _envelope(**answer)
    assert _envelope(**abstain)
    with pytest.raises(ValidationError, match="abstain carries no source"):
        _envelope(sources=(_source(),), **abstain)


def test_only_farewell_and_handoff_end_a_session() -> None:
    """A conversation is not ended by a clarification."""
    assert _envelope(intent=Intent.FAREWELL, template_id=TemplateId.FAREWELL, end_session=True)
    with pytest.raises(ValidationError, match="only farewell and handoff"):
        _envelope(end_session=True)


def test_facts_bound_their_lists_and_keep_the_count_honest() -> None:
    """At most five candidates are listed, and the total never falls below what is listed."""
    with pytest.raises(ValidationError):
        DisputeFacts(transactions=(_transaction(),) * 6, candidate_count=6)
    with pytest.raises(ValidationError, match="candidate_count is below"):
        DisputeFacts(transactions=(_transaction(),), candidate_count=0)

    assert DisputeFacts(transactions=(_transaction(),), candidate_count=9).candidate_count == 9


def test_a_transaction_may_have_no_merchant_and_never_a_full_card_number() -> None:
    """A missing merchant stays missing; a product shows four digits and nothing longer."""
    assert _transaction(merchant=None).merchant is None
    with pytest.raises(ValidationError):
        ProductLabel(name="Visa Classic", last4="4111111111111111")


@pytest.mark.parametrize(
    "amount, currency",
    [
        (Decimal("-1.00"), "MXN"),
        (Decimal("12.345"), "MXN"),
        (Decimal("12.00"), "mxn"),
        (Decimal("12.00"), "PESOS"),
    ],
)
def test_money_needs_a_valid_amount_and_currency_code(amount: Decimal, currency: str) -> None:
    """An amount is non-negative with at most two decimals and always has a three-letter code."""
    with pytest.raises(ValidationError):
        Money(amount=amount, currency=currency)


def test_a_source_reference_has_one_title_per_language() -> None:
    """A citation exists in every reply language, exactly once."""
    source = _source()

    assert source.title_for("pt") == "Prazos para contestar"
    with pytest.raises(ValidationError, match="exactly one title"):
        SourceRef(section_id="filing-windows", titles=_titles()[:2], corpus_version="2")
    with pytest.raises(ValidationError, match="exactly one title"):
        SourceRef(section_id="filing-windows", titles=_titles() + _titles()[:1], corpus_version="2")


def test_a_risk_score_lies_within_its_interval() -> None:
    """A score outside its own uncertainty interval is a malformed record."""
    assert RiskEvidence(score=0.4, interval_low=0.3, interval_high=0.5, base_rate=0.001)
    with pytest.raises(ValidationError, match="between interval_low and interval_high"):
        RiskEvidence(score=0.9, interval_low=0.3, interval_high=0.5, base_rate=0.001)


def test_tool_unavailable_is_a_typed_result() -> None:
    """A tool that cannot answer says which tool and why, from a closed set of causes."""
    result = ToolUnavailable(tool="list_transactions", cause="timeout", retryable=True)

    assert result.retryable
    with pytest.raises(ValidationError):
        ToolUnavailable(tool="list_transactions", cause="boom")


# -----------------------------------------------------------------------------
# Agent-only detail
# -----------------------------------------------------------------------------


def _routed_envelope() -> Envelope:
    return _envelope(
        intent=Intent.HANDOFF,
        end_session=True,
        template_id=TemplateId.HANDOFF_REVIEW,
        next_expected=None,
        decisions=(
            Decision(
                outcome=Outcome.ESCALATE,
                customer_reason=CustomerReason.NEEDS_REVIEW,
                policy_version="2",
            ),
        ),
        agent_only=AgentOnly(
            decisions=(
                AgentDecision(
                    reason_code=ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD,
                    triggers=(ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD,),
                ),
            ),
            nlu_confidence=0.91,
            risk=RiskEvidence(score=0.4, interval_low=0.3, interval_high=0.5, base_rate=0.001),
        ),
    )


def test_the_renderer_view_drops_the_agent_only_detail() -> None:
    """What the renderer receives holds neither the reason code nor the routing inputs."""
    envelope = _routed_envelope()

    view = envelope.render_view()
    dumped = json.dumps(view.model_dump(mode="json"))

    assert isinstance(view, RenderEnvelope)
    assert not isinstance(view, Envelope)
    assert "agent_only" not in dumped
    assert ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD.value not in dumped
    assert "0.91" not in dumped
    assert envelope.agent_only is not None


def test_every_routing_reason_reaches_the_customer_as_the_same_reason() -> None:
    """The customer side of a decision is one closed value for every trigger."""
    customer_side = {
        CustomerReason.NEEDS_REVIEW.value,
    }
    dumped = _routed_envelope().render_view().model_dump(mode="json")["decisions"][0]

    assert dumped["customer_reason"] in customer_side
    assert set(dumped) == {"outcome", "customer_reason", "policy_version", "requires_confirmation"}


def test_the_renderer_schema_names_no_agent_only_or_raw_field() -> None:
    """No field that could carry routing detail or raw material exists in what a renderer gets."""
    names = set(_field_names(RenderEnvelope.model_json_schema()))

    assert not names & _AGENT_ONLY_NAMES
    assert not names & _RAW_MATERIAL_NAMES


def test_the_full_envelope_round_trips_through_json() -> None:
    """The audit form of an envelope is read back as the same envelope."""
    envelope = _routed_envelope()

    assert Envelope.model_validate_json(envelope.model_dump_json()) == envelope


# -----------------------------------------------------------------------------
# Understanding
# -----------------------------------------------------------------------------


def test_unusable_understanding_is_one_known_value() -> None:
    """Output that did not arrive or validate is nothing understood, at zero confidence."""
    result = NluResult.unusable()

    assert result.intent is NluIntent.UNCLEAR
    assert result.confidence == 0.0
    assert result.transaction.is_empty


def test_understanding_rejects_out_of_range_values() -> None:
    """Confidence is a rate, a choice is one of five, free text is bounded."""
    with pytest.raises(ValidationError):
        NluResult(intent=NluIntent.UNCLEAR, confidence=1.2)
    with pytest.raises(ValidationError):
        NluResult(intent=NluIntent.CHOICE, confidence=0.9, choice=6)
    with pytest.raises(ValidationError):
        NluResult(intent=NluIntent.FILE_DISPUTE, confidence=0.9, detail="x" * 501)
    with pytest.raises(ValidationError):
        NluResult.model_validate({"intent": "file_dispute", "confidence": 0.9, "extra": 1})


def test_a_transaction_hint_knows_when_it_holds_nothing() -> None:
    """A currency alone is not something to search by."""
    assert TransactionHint().is_empty
    assert TransactionHint(currency="MXN").is_empty
    assert not TransactionHint(amount=Decimal("500")).is_empty


# -----------------------------------------------------------------------------
# Handoff packet
# -----------------------------------------------------------------------------


def test_a_packet_is_valid_and_complete() -> None:
    """A packet carries its request, evidence and both clocks."""
    packet = _packet(
        open_questions=(OpenQuestion(slot=Slot.REASON, attempts=2),),
        attempted_action=ActionRecord(action="create_case", result="not_confirmed"),
    )

    assert packet.reference_date == _DOMAIN_DATE
    assert packet.created_at.utcoffset() == timedelta(0)


def test_the_packet_language_flag_follows_the_language() -> None:
    """Portuguese and English are flagged; Spanish is not."""
    with pytest.raises(ValidationError, match="needs_language_routing"):
        _packet(needs_language_routing=False)
    with pytest.raises(ValidationError, match="needs_language_routing"):
        _packet(language="es", needs_language_routing=True)

    assert _packet(language="es", needs_language_routing=False).language == "es"


def test_the_packet_instant_is_utc() -> None:
    """The real instant is UTC, and a naive time is refused."""
    bogota = timezone(timedelta(hours=-5))

    with pytest.raises(ValidationError, match="must be in UTC"):
        _packet(created_at=datetime(2026, 9, 26, 10, 0, tzinfo=bogota))
    with pytest.raises(ValidationError):
        _packet(created_at=datetime(2026, 9, 26, 15, 0))


def test_the_customer_appears_masked() -> None:
    """A first name and a masked identifier; a plain identifier is refused."""
    with pytest.raises(ValidationError):
        CustomerLabel(first_name="Ana", masked_id="1234567890")


def test_the_packet_schema_holds_no_raw_material() -> None:
    """There is no field for a transcript, a message, a document or a card number."""
    names = set(_field_names(HandoffPacket.model_json_schema()))

    assert not names & _RAW_MATERIAL_NAMES


# -----------------------------------------------------------------------------
# Customer API
# -----------------------------------------------------------------------------


def test_a_turn_request_cannot_name_a_customer_a_session_or_a_date() -> None:
    """Identity and the domain date come from the service, never from the request."""
    valid = {"turn_id": "turn-0001", "text": "me cobraron mal"}

    assert TurnRequest.model_validate(valid)
    for extra in ("customer_id", "session_id", "domain_date"):
        with pytest.raises(ValidationError):
            TurnRequest.model_validate({**valid, extra: "x"})


def test_a_turn_request_bounds_its_text_and_identifier() -> None:
    """Empty and oversized text, and a malformed turn identifier, are refused."""
    with pytest.raises(ValidationError):
        TurnRequest(turn_id="turn-0001", text="")
    with pytest.raises(ValidationError):
        TurnRequest(turn_id="turn-0001", text="x" * (MAX_TEXT_LENGTH + 1))
    with pytest.raises(ValidationError):
        TurnRequest(turn_id="short", text="hola")


def test_the_turn_response_carries_no_envelope_or_routing_detail() -> None:
    """What the client receives has no reason code, no routing input and no envelope."""
    names = set(_field_names(TurnResponse.model_json_schema()))

    assert not names & _AGENT_ONLY_NAMES
    assert not names & _RAW_MATERIAL_NAMES
    assert {"reply", "reference_date_line", "demo_notice"} <= names
    assert "facts" not in names
    assert "decisions" not in names


def test_readiness_reports_the_reference_date_and_its_origin() -> None:
    """A deployment on an explicit date cannot be mistaken for live data."""
    payload = ReadinessPayload(
        status="ready",
        service_version="0.1.0",
        environment="prod",
        reference_date=_DOMAIN_DATE,
        reference_date_origin=ReferenceDateOrigin.SETTING,
    )

    assert payload.model_dump(mode="json")["reference_date_origin"] == "setting"
    with pytest.raises(ValidationError):
        ReadinessPayload.model_validate({**payload.model_dump(), "reference_date_origin": "guess"})


# -----------------------------------------------------------------------------
# Console
# -----------------------------------------------------------------------------


def test_fraud_and_card_loss_tickets_sort_first() -> None:
    """Only those two triggers are priority."""
    priority = {t for t in HandoffTrigger if is_priority(t)}

    assert priority == {HandoffTrigger.FRAUD_REPORT, HandoffTrigger.CARD_LOSS}


def test_a_ticket_detail_holds_the_packet_and_a_timeline_without_message_text() -> None:
    """Both clocks are present and no timeline field can hold a message."""
    detail = TicketDetail(
        item=_queue_item(),
        packet=_packet(),
        timeline=(
            TimelineEntry(
                occurred_at=datetime(2026, 9, 26, 15, 0, tzinfo=UTC),
                trace_id="trace-1",
                intent=Intent.HANDOFF,
                state_before="collect_reason",
                state_after="handed_off",
                render_mode="template",
                reason_code=ReasonCode.ESCALATE_FRAUD_CLAIM,
                policy_version="2",
            ),
        ),
    )

    names = set(_field_names(TicketDetail.model_json_schema()))
    assert detail.item.reference_date == detail.packet.reference_date
    assert not names & _RAW_MATERIAL_NAMES
    with pytest.raises(ValidationError):
        TimelineEntry.model_validate({**detail.timeline[0].model_dump(), "render_mode": "other"})


# -----------------------------------------------------------------------------
# Published fixtures
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["es", "pt", "en"])
def test_every_scripted_envelope_fixture_validates_in_its_language(lang: str) -> None:
    """The recorded envelopes other code is built against agree with the contract."""
    flows = json.loads((_FIXTURES / f"scripted_flows.{lang}.json").read_text())

    assert {"file_dispute", "policy_answer", "abstain", "fraud_handoff"} == set(flows)
    for steps in flows.values():
        for step in steps:
            envelope = Envelope.model_validate(step)
            assert envelope.lang == lang
            assert envelope.domain_date == _DOMAIN_DATE


@pytest.mark.parametrize("lang", ["es", "pt", "en"])
def test_the_committed_fixture_files_match_their_builders(lang: Lang) -> None:
    """A fixture cannot drift from the contract: the files are what the builders produce."""
    assert scripted_flows.path_for(lang).read_text() == scripted_flows.render(lang)
