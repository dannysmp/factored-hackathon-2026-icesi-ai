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
from enum import StrEnum  # Closed sets pinned by value
from pathlib import Path  # Fixture location
from typing import Any  # Loosely typed JSON

# Third-party libraries
import pytest  # Test runner and fixtures
from pydantic import ValidationError  # Boundary failures

# Local modules
from app.domain.policy.models import (
    DisputeCategory,
    Outcome,
    ReasonCode,
    TransactionStatus,
)
from contracts import service_v1
from contracts.service_v1.api import (
    MAX_TEXT_LENGTH,
    Choice,
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
    CUSTOMER_REASON_OF,
    INTENT_ALLOWED_FIELDS,
    INTENT_REQUIRED_FIELDS,
    TEMPLATE_INTENTS,
    AgentDecision,
    AgentOnly,
    CaseFact,
    CustomerReason,
    DateSource,
    DateToConfirm,
    Decision,
    DisputeFacts,
    Envelope,
    GroundedField,
    Intent,
    Lang,
    LocalizedTitle,
    Money,
    PolicyValue,
    ProductLabel,
    RenderEnvelope,
    RiskEvidence,
    Slot,
    SourceRef,
    TemplateId,
    ToolUnavailable,
    TransactionFact,
    WindowFact,
    outcome_of,
)
from contracts.service_v1.handoff import (
    ActionRecord,
    CustomerLabel,
    Evidence,
    HandoffPacket,
    HandoffTrigger,
    OpenQuestion,
)
from contracts.service_v1.nlu import (
    ConfirmationAnswer,
    NluIntent,
    NluResult,
    TransactionHint,
)
from contracts.service_v1.verification import (
    CandidateReply,
    RejectionReason,
    SlotValue,
    SlotValues,
    VerifierResult,
)
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
    """One synthetic transaction, with any field replaced by ``changes``."""
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
    """The three titles of the sample policy section."""
    return (
        LocalizedTitle(lang="es", text="Plazos para disputar"),
        LocalizedTitle(lang="pt", text="Prazos para contestar"),
        LocalizedTitle(lang="en", text="Filing windows"),
    )


def _source() -> SourceRef:
    """A policy section with a title in every language."""
    return SourceRef(section_id="filing-windows", titles=_titles(), corpus_version="2")


def _eligible_decision() -> Decision:
    """An eligible decision that needs the customer's confirmation."""
    return Decision(
        outcome=Outcome.ELIGIBLE,
        customer_reason=CustomerReason.ELIGIBLE,
        policy_version="2",
        requires_confirmation=True,
    )


def _envelope(**changes: Any) -> Envelope:
    """A valid clarification envelope, with any field replaced by ``changes``."""
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
    """A valid fraud handoff packet in Portuguese, with any field replaced by ``changes``."""
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
    """A queue row that describes ``_packet``, with any field replaced by ``changes``."""
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
        envelope.lang = "en"  # type: ignore[misc]  # assignment to a frozen model is the point


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


def test_a_refusal_never_renders_from_the_model() -> None:
    """A refusal always carries a fixed text; the model is never asked to phrase one."""
    with pytest.raises(ValidationError, match="a refusal always renders"):
        _envelope(intent=Intent.REFUSE, render_mode="model", template_id=None)

    assert _envelope(intent=Intent.REFUSE, template_id=TemplateId.REFUSE_UNSUPPORTED)


def test_every_intent_names_its_grounded_fields() -> None:
    """An intent added without an entry in either table fails here, and required implies allowed."""
    assert set(INTENT_ALLOWED_FIELDS) == set(Intent)
    assert set(INTENT_REQUIRED_FIELDS) == set(Intent)
    for intent in Intent:
        assert INTENT_REQUIRED_FIELDS[intent] <= INTENT_ALLOWED_FIELDS[intent]
        assert INTENT_ALLOWED_FIELDS[intent] <= set(GroundedField)


def test_a_refused_intent_names_no_grounded_field() -> None:
    """A refusal never renders from the model, so it names nothing to substitute either."""
    assert INTENT_ALLOWED_FIELDS[Intent.REFUSE] == frozenset()
    assert INTENT_REQUIRED_FIELDS[Intent.REFUSE] == frozenset()


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


def test_exactly_farewell_and_handoff_end_a_session() -> None:
    """A clarification never ends a conversation, and a farewell always does."""
    assert _envelope(intent=Intent.FAREWELL, template_id=TemplateId.FAREWELL, end_session=True)
    with pytest.raises(ValidationError, match="exactly farewell and handoff"):
        _envelope(end_session=True)
    with pytest.raises(ValidationError, match="exactly farewell and handoff"):
        _envelope(intent=Intent.FAREWELL, template_id=TemplateId.FAREWELL)


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


def test_a_transaction_may_have_no_amount() -> None:
    """An amount the source never gave stays absent, never invented."""
    assert _transaction(amount=None).amount is None


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
# Verification
# -----------------------------------------------------------------------------


def test_a_candidate_reply_carries_only_the_models_raw_text() -> None:
    """The candidate has no intent, fact or decision of its own to check anything against."""
    candidate = CandidateReply(raw_text="I will file a dispute for {{amount}}.")

    assert candidate.raw_text
    with pytest.raises(ValidationError):
        CandidateReply(raw_text="")
    with pytest.raises(ValidationError):
        CandidateReply(raw_text="ok", intent=Intent.CLARIFY)  # type: ignore[call-arg]


def test_slot_values_hold_one_entry_per_citation_in_order() -> None:
    """A field cited twice (two cases) is two entries, consumed in the order they are listed."""
    values = SlotValues(
        entries=(
            SlotValue(field=GroundedField.CASE_NUMBER, value="D-1"),
            SlotValue(field=GroundedField.CASE_NUMBER, value="D-2"),
        )
    )

    assert [entry.value for entry in values.entries] == ["D-1", "D-2"]


def test_a_verifier_result_agrees_with_its_own_outcome() -> None:
    """Rejected names why and carries no text; accepted carries the text and names nothing."""
    assert VerifierResult(outcome="accepted", rendered_text="ok")
    assert VerifierResult(outcome="rejected", reasons=(RejectionReason.DIGIT_OUTSIDE_SLOT,))
    with pytest.raises(ValidationError, match="names at least one reason"):
        VerifierResult(outcome="rejected")
    with pytest.raises(ValidationError, match="carries no rendered text"):
        VerifierResult(
            outcome="rejected",
            rendered_text="ok",
            reasons=(RejectionReason.DIGIT_OUTSIDE_SLOT,),
        )
    with pytest.raises(ValidationError, match="names no reason"):
        VerifierResult(
            outcome="accepted",
            rendered_text="ok",
            reasons=(RejectionReason.DIGIT_OUTSIDE_SLOT,),
        )
    with pytest.raises(ValidationError, match="carries the rendered text"):
        VerifierResult(outcome="accepted")


# -----------------------------------------------------------------------------
# Agent-only detail
# -----------------------------------------------------------------------------


def _routed_envelope() -> Envelope:
    return _envelope(
        intent=Intent.HANDOFF,
        end_session=True,
        template_id=TemplateId.HANDOFF_REVIEW,
        next_expected=None,
        facts=DisputeFacts(ticket_ref="T-100"),
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


def test_every_reason_code_has_a_plain_reason_and_every_escalation_shares_one() -> None:
    """No decision reaches a customer unexplained, and no trigger can be told from another."""
    assert set(CUSTOMER_REASON_OF) == set(ReasonCode)
    escalations = {code for code in ReasonCode if code.value.startswith("escalate_")}

    assert {CUSTOMER_REASON_OF[code] for code in escalations} == {CustomerReason.NEEDS_REVIEW}
    assert all(outcome_of(code) is Outcome.ESCALATE for code in escalations)
    assert outcome_of(ReasonCode.ELIGIBLE) is Outcome.ELIGIBLE
    assert outcome_of(ReasonCode.FILING_WINDOW_EXPIRED) is Outcome.INELIGIBLE


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
        policy_version="2",
        policy_digest="a" * 64,
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

    assert set(flows) == {
        "file_dispute",
        "policy_answer",
        "abstain",
        "ineligible_window",
        "refusal",
        "fraud_handoff",
    }
    for steps in flows.values():
        for step in steps:
            envelope = Envelope.model_validate(step)
            assert envelope.lang == lang
            assert envelope.domain_date == _DOMAIN_DATE


@pytest.mark.parametrize("lang", ["es", "pt", "en"])
def test_the_committed_fixture_files_match_their_builders(lang: Lang) -> None:
    """A fixture cannot drift from the contract: the files are what the builders produce."""
    assert scripted_flows.path_for(lang).read_text() == scripted_flows.render(lang)


# -----------------------------------------------------------------------------
# Separation of the two views
# -----------------------------------------------------------------------------


def test_a_full_envelope_is_never_accepted_as_the_renderer_view() -> None:
    """The renderer's type is not a supertype of the full envelope, at runtime or in a check."""
    envelope = _routed_envelope()

    assert not isinstance(envelope, RenderEnvelope)
    with pytest.raises(ValidationError):
        RenderEnvelope.model_validate(envelope)
    with pytest.raises(ValidationError):
        RenderEnvelope.model_validate(envelope.model_dump())


# -----------------------------------------------------------------------------
# Decisions agree with themselves
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome, reason",
    [
        (Outcome.ESCALATE, CustomerReason.DECLINED),
        (Outcome.ESCALATE, CustomerReason.ELIGIBLE),
        (Outcome.ELIGIBLE, CustomerReason.NEEDS_REVIEW),
        (Outcome.INELIGIBLE, CustomerReason.ELIGIBLE),
        (Outcome.INELIGIBLE, CustomerReason.NEEDS_REVIEW),
    ],
)
def test_an_outcome_and_its_plain_reason_must_agree(
    outcome: Outcome, reason: CustomerReason
) -> None:
    """A decision cannot escalate for a declined transaction or refuse for review."""
    with pytest.raises(ValidationError, match="does not agree"):
        Decision(outcome=outcome, customer_reason=reason, policy_version="2")


@pytest.mark.parametrize("outcome", [Outcome.INELIGIBLE, Outcome.ESCALATE])
def test_only_an_eligible_decision_can_require_confirmation(outcome: Outcome) -> None:
    """A confirmation prompt exists only for something that can be filed."""
    reason = (
        CustomerReason.DECLINED if outcome is Outcome.INELIGIBLE else CustomerReason.NEEDS_REVIEW
    )

    with pytest.raises(ValidationError, match="only an eligible decision"):
        Decision(
            outcome=outcome, customer_reason=reason, policy_version="2", requires_confirmation=True
        )


def test_every_ineligible_reason_is_accepted_for_an_ineligible_outcome() -> None:
    """The six refusal reasons all pair with the ineligible outcome."""
    reasons = set(CustomerReason) - {CustomerReason.ELIGIBLE, CustomerReason.NEEDS_REVIEW}

    assert len(reasons) == 6
    for reason in reasons:
        assert Decision(outcome=Outcome.INELIGIBLE, customer_reason=reason, policy_version="2")


def _routed_with_agent_reasons(*codes: ReasonCode) -> Envelope:
    """The routed envelope with agent decisions for ``codes``, in order."""
    routed = _routed_envelope()
    fields = {name: getattr(routed, name) for name in type(routed).model_fields}
    agent = AgentOnly(decisions=tuple(AgentDecision(reason_code=code) for code in codes))
    return Envelope(**{**fields, "agent_only": agent})


def test_agent_detail_pairs_with_the_decisions_by_index() -> None:
    """The agent's reason code gives the plain reason of the decision at its position."""
    assert _routed_with_agent_reasons(ReasonCode.ESCALATE_FRAUD_CLAIM)
    with pytest.raises(ValidationError, match="pair one to one"):
        _routed_with_agent_reasons(ReasonCode.ESCALATE_FRAUD_CLAIM, ReasonCode.ESCALATE_RISK_SCORE)
    with pytest.raises(ValidationError, match="does not match its decision"):
        _routed_with_agent_reasons(ReasonCode.FILING_WINDOW_EXPIRED)


# -----------------------------------------------------------------------------
# Fixed texts belong to their intents
# -----------------------------------------------------------------------------


def test_every_fixed_text_names_the_intents_that_may_carry_it() -> None:
    """A text added without an entry fails here, and each entry names a real intent."""
    assert set(TEMPLATE_INTENTS) == set(TemplateId)
    assert all(intents and intents <= set(Intent) for intents in TEMPLATE_INTENTS.values())


def test_a_fixed_text_is_refused_under_an_intent_it_does_not_belong_to() -> None:
    """A farewell text cannot carry a clarification, nor a refusal text a handoff."""
    with pytest.raises(ValidationError, match="does not belong to the intent"):
        _envelope(template_id=TemplateId.FAREWELL)
    with pytest.raises(ValidationError, match="does not belong to the intent"):
        _envelope(intent=Intent.REFUSE, template_id=TemplateId.HANDOFF_FRAUD)


def test_an_ineligible_reply_and_a_refusal_have_an_intent_of_their_own() -> None:
    """The expired-window path and an unsupported request are expressible."""
    ineligible = Decision(
        outcome=Outcome.INELIGIBLE,
        customer_reason=CustomerReason.WINDOW_EXPIRED,
        policy_version="2",
    )
    window = WindowFact(days_allowed=60, age_days=137, deadline=date(2026, 4, 2))

    assert _envelope(
        intent=Intent.INELIGIBLE,
        template_id=TemplateId.INELIGIBLE,
        facts=DisputeFacts(window=window),
        decisions=(ineligible,),
    )
    assert _envelope(intent=Intent.REFUSE, template_id=TemplateId.REFUSE_UNSUPPORTED)
    with pytest.raises(ValidationError, match="ineligible requires only ineligible"):
        _envelope(intent=Intent.INELIGIBLE, template_id=TemplateId.INELIGIBLE)
    with pytest.raises(ValidationError, match="ineligible requires only ineligible"):
        _envelope(
            intent=Intent.INELIGIBLE,
            template_id=TemplateId.INELIGIBLE,
            decisions=(ineligible, _eligible_decision()),
        )


def test_the_window_deadline_matches_the_domain_date_and_the_days_remaining() -> None:
    """The deadline is the domain date plus the days left in the window — negative once the
    window has expired, which the deadline must reflect, not just the flat filing period."""
    ineligible = Decision(
        outcome=Outcome.INELIGIBLE,
        customer_reason=CustomerReason.WINDOW_EXPIRED,
        policy_version="2",
    )
    consistent = WindowFact(days_allowed=60, age_days=137, deadline=date(2026, 4, 2))
    assert _envelope(
        intent=Intent.INELIGIBLE,
        template_id=TemplateId.INELIGIBLE,
        facts=DisputeFacts(window=consistent),
        decisions=(ineligible,),
    )

    inconsistent = WindowFact(days_allowed=60, age_days=137, deadline=date(2026, 4, 3))
    with pytest.raises(ValidationError, match="deadline does not match"):
        _envelope(
            intent=Intent.INELIGIBLE,
            template_id=TemplateId.INELIGIBLE,
            facts=DisputeFacts(window=inconsistent),
            decisions=(ineligible,),
        )


def test_confirm_filing_needs_its_selection_among_the_listed_transactions() -> None:
    """A confirmation names a transaction the customer was shown, and files nothing else."""
    facts = DisputeFacts(
        transactions=(_transaction(),),
        candidate_count=1,
        selected_ref="tx-9999",
        category=DisputeCategory.UNRECOGNIZED_CHARGE,
    )
    escalate = Decision(
        outcome=Outcome.ESCALATE, customer_reason=CustomerReason.NEEDS_REVIEW, policy_version="2"
    )
    common: dict[str, Any] = {
        "intent": Intent.CONFIRM_FILING,
        "template_id": TemplateId.CONFIRM_FILING,
        "next_expected": Slot.CONFIRMATION,
    }

    with pytest.raises(ValidationError, match="among those listed"):
        _envelope(facts=facts, decisions=(_eligible_decision(),), **common)
    listed = facts.model_copy(update={"selected_ref": "tx-1001"})
    with pytest.raises(ValidationError, match="only eligible decisions"):
        _envelope(facts=listed, decisions=(_eligible_decision(), escalate), **common)


def test_dispute_status_needs_cases_unless_it_says_there_are_none() -> None:
    """Listing cases needs cases; the no-case text is the one reply without them."""
    case = CaseFact(
        case_number="D-1", status="Open", filed_on=_DOMAIN_DATE, transaction_ref="tx-1001"
    )

    assert _envelope(
        intent=Intent.DISPUTE_STATUS,
        template_id=TemplateId.NO_CASE_FOUND,
    )
    assert _envelope(
        intent=Intent.DISPUTE_STATUS,
        template_id=TemplateId.DISPUTE_STATUS,
        facts=DisputeFacts(cases=(case,)),
    )
    with pytest.raises(ValidationError, match="dispute_status requires cases"):
        _envelope(intent=Intent.DISPUTE_STATUS, template_id=TemplateId.DISPUTE_STATUS)


def test_dispute_status_can_say_there_are_none_from_model_mode_facts_alone() -> None:
    """Model mode never sets a template_id, so the no-case state rests on the cases fact itself."""
    assert (
        _envelope(intent=Intent.DISPUTE_STATUS, render_mode="model", template_id=None).facts.cases
        == ()
    )


def test_a_case_is_not_expected_to_answer_before_it_was_filed() -> None:
    """The expected response date is on or after the filing date."""
    with pytest.raises(ValidationError, match="before filed_on"):
        CaseFact(
            case_number="D-1",
            status="Open",
            filed_on=_DOMAIN_DATE,
            transaction_ref="tx-1001",
            expected_response_on=_DOMAIN_DATE - timedelta(days=1),
        )


# -----------------------------------------------------------------------------
# A handoff states what happened
# -----------------------------------------------------------------------------


def _handoff(**changes: Any) -> Envelope:
    """A valid routed handoff envelope, with any field replaced by ``changes``."""
    values: dict[str, Any] = {
        "intent": Intent.HANDOFF,
        "end_session": True,
        "template_id": TemplateId.HANDOFF_REVIEW,
        "next_expected": None,
        "facts": DisputeFacts(ticket_ref="T-100"),
        "decisions": _routed_envelope().decisions,
    }
    return _envelope(**{**values, **changes})


def test_a_handoff_ends_the_session_and_names_its_ticket() -> None:
    """A handoff without ticket, or one that leaves the session open, does not build."""
    assert _handoff()
    with pytest.raises(ValidationError, match="exactly farewell and handoff"):
        _handoff(end_session=False)
    with pytest.raises(ValidationError, match="names its ticket exactly when"):
        _handoff(facts=DisputeFacts())


def test_a_handoff_that_was_not_registered_names_no_ticket() -> None:
    """The fallback statement never quotes a ticket that does not exist."""
    unregistered = _handoff(
        template_id=TemplateId.HANDOFF_NOT_REGISTERED, facts=DisputeFacts(), decisions=()
    )

    assert unregistered.facts.ticket_ref is None
    with pytest.raises(ValidationError, match="names its ticket exactly when"):
        _handoff(template_id=TemplateId.HANDOFF_NOT_REGISTERED)


def test_a_handoff_can_say_nothing_was_registered_from_model_mode_facts_alone() -> None:
    """Model mode never sets a template_id, so "not registered" rests on the ticket fact itself."""
    unregistered = _handoff(
        render_mode="model", template_id=None, facts=DisputeFacts(), decisions=()
    )

    assert unregistered.facts.ticket_ref is None


def test_a_routed_handoff_needs_an_escalate_decision_and_no_handoff_reads_as_eligible() -> None:
    """The review and fraud texts rest on a decision to escalate; nothing hands over as eligible."""
    with pytest.raises(ValidationError, match="requires an escalate decision"):
        _handoff(decisions=())
    with pytest.raises(ValidationError, match="no eligible decision"):
        _handoff(decisions=(_eligible_decision(),))
    assert _handoff(template_id=TemplateId.HANDOFF_REQUESTED, decisions=())


# -----------------------------------------------------------------------------
# Free text
# -----------------------------------------------------------------------------

# A card-shaped digit run. Free text is not screened for it: that redaction control lives at the
# masking serializer (the egress boundary), not in these field validators; a contract field only
# ever refuses a control character.
_CARD_NUMBER = "4111111111111111"


@pytest.mark.parametrize(
    "text",
    [
        f"Tienda {_CARD_NUMBER}",
        f"cargo con {_CARD_NUMBER}",
        f"Card {_CARD_NUMBER} was used",
    ],
)
def test_free_text_fields_stay_a_contract_shape_check_only(text: str) -> None:
    """A bounded free-text field refuses a control character and nothing about its content."""
    assert _transaction(merchant=text).merchant
    assert NluResult(intent=NluIntent.FILE_DISPUTE, confidence=0.9, detail=text)
    assert TransactionHint(merchant=text)
    assert _packet(request_summary=text)
    assert ActionRecord(action=text, result="none")
    assert Choice(number=1, label=text)


def test_free_text_refuses_control_characters() -> None:
    """A NUL or an escape sequence is refused; ordinary text, digits included, is not."""
    assert _transaction(merchant="Pedido 1234567").merchant
    for text in ("Tienda\x00Sol", "Tienda\x1bSol", "Tienda\nSol"):
        with pytest.raises(ValidationError, match="control characters"):
            _transaction(merchant=text)


def test_a_message_may_not_be_blank_or_hold_a_control_character() -> None:
    """Whitespace-only text and NUL are refused; a line break and a typed card number are not."""
    for text in ("   ", "\n\t ", "hola\x00", "hola\x1b[31m"):
        with pytest.raises(ValidationError):
            TurnRequest(turn_id="turn-0001", text=text)

    assert TurnRequest(turn_id="turn-0001", text="línea uno\nlínea dos")
    assert TurnRequest(turn_id="turn-0001", text=f"mi tarjeta es {_CARD_NUMBER}")


# -----------------------------------------------------------------------------
# Console consistency
# -----------------------------------------------------------------------------


def test_the_priority_flag_follows_the_trigger() -> None:
    """A fraud item cannot be shown as ordinary, nor an ordinary item as priority."""
    with pytest.raises(ValidationError, match="priority must be true exactly"):
        _queue_item(priority=False)
    with pytest.raises(ValidationError, match="priority must be true exactly"):
        _queue_item(trigger=HandoffTrigger.AMOUNT_REVIEW, priority=True)

    assert _queue_item(trigger=HandoffTrigger.AMOUNT_REVIEW, priority=False)


def test_instants_of_record_are_utc_everywhere() -> None:
    """The queue row and the timeline hold UTC like the packet does."""
    bogota = timezone(timedelta(hours=-5))

    with pytest.raises(ValidationError, match="in UTC"):
        _queue_item(created_at=datetime(2026, 9, 26, 10, 0, tzinfo=bogota))
    with pytest.raises(ValidationError, match="in UTC"):
        TimelineEntry(
            occurred_at=datetime(2026, 9, 26, 10, 0, tzinfo=bogota),
            trace_id="trace-1",
            intent=Intent.HANDOFF,
            state_before="a",
            state_after="b",
            render_mode="template",
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"ticket_ref": "T-999"},
        {"trigger": HandoffTrigger.CARD_LOSS},
        {"language": "es"},
        {"category": DisputeCategory.WRONG_AMOUNT},
        {"reference_date": date(2026, 6, 17)},
        {"created_at": datetime(2026, 9, 26, 16, 0, tzinfo=UTC)},
    ],
)
def test_a_ticket_row_must_describe_its_packet(changes: dict[str, Any]) -> None:
    """Each field the row and the packet both state must agree."""
    with pytest.raises(ValidationError, match="does not describe the packet"):
        TicketDetail(item=_queue_item(**changes), packet=_packet(), timeline=())

    assert TicketDetail(item=_queue_item(), packet=_packet(), timeline=())


# -----------------------------------------------------------------------------
# Handoff packet completeness
# -----------------------------------------------------------------------------


def test_a_customer_request_for_a_person_needs_no_facts_actions_or_reason_codes() -> None:
    """The parts that may be empty are the ones a request for a person does not have."""
    packet = _packet(
        trigger=HandoffTrigger.CUSTOMER_REQUEST,
        category=None,
        evidence=Evidence(reason_codes=(), policy_version="2"),
    )

    assert not packet.verified_facts
    assert not packet.actions
    assert not packet.open_questions
    assert not packet.evidence.reason_codes
    with pytest.raises(ValidationError):
        _packet(evidence=Evidence(reason_codes=(), policy_version=""))


# -----------------------------------------------------------------------------
# States the conversation must be able to express
# -----------------------------------------------------------------------------


def test_a_second_dispute_and_a_date_awaiting_confirmation_can_be_stated() -> None:
    """A remembered second dispute and a resolved date shown for confirmation are expressible."""
    facts = DisputeFacts(
        transactions=(_transaction(),),
        candidate_count=1,
        pending_disputes=1,
        date_to_confirm=DateToConfirm(resolved_on=date(2026, 6, 17), source=DateSource.RELATIVE),
    )
    envelope = _envelope(
        intent=Intent.PRESENT_TRANSACTIONS,
        template_id=TemplateId.PRESENT_ONE,
        next_expected=Slot.TRANSACTION_CHOICE,
        facts=facts,
    )

    assert envelope.facts.pending_disputes == 1
    assert envelope.facts.date_to_confirm is not None
    assert envelope.facts.date_to_confirm.source is DateSource.RELATIVE
    with pytest.raises(ValidationError):
        DisputeFacts(pending_disputes=6)


def test_an_evidence_list_for_a_category_fits_a_policy_value() -> None:
    """The evidence identifiers of a category, joined, fit the value and the entry limits."""
    evidence = ",".join(f"evidence_item_{n:02d}" for n in range(8))
    facts = DisputeFacts(policy_values=(PolicyValue(name="evidence_required", value=evidence),))

    assert len(evidence) <= 200
    assert facts.policy_values[0].value == evidence
    assert (
        len(DisputeFacts(policy_values=(PolicyValue(name="x", value="y"),) * 16).policy_values)
        == 16
    )


# -----------------------------------------------------------------------------
# Understanding, API and readiness
# -----------------------------------------------------------------------------


def test_a_slot_comes_only_with_its_own_intent() -> None:
    """A confirmation, a choice and a requested language are read only for their own intent."""
    assert NluResult(
        intent=NluIntent.CONFIRMATION, confidence=0.9, confirmation=ConfirmationAnswer.YES
    )
    with pytest.raises(ValidationError, match="confirmation is read exactly"):
        NluResult(
            intent=NluIntent.FILE_DISPUTE, confidence=0.9, confirmation=ConfirmationAnswer.YES
        )
    with pytest.raises(ValidationError, match="choice is read exactly"):
        NluResult(intent=NluIntent.FILE_DISPUTE, confidence=0.9, choice=2)
    with pytest.raises(ValidationError, match="requested_language is read exactly"):
        NluResult(intent=NluIntent.SMALL_TALK, confidence=0.9, requested_language="pt")


def test_the_response_version_choices_and_readiness_are_closed() -> None:
    """Only version 1 exists, choices number from one, and readiness reports the policy loaded."""
    base: dict[str, Any] = {
        "turn_id": "turn-0001",
        "conversation_id": "c-1",
        "state_version": 1,
        "lang": "es",
        "reply": "Hola",
        "reference_date_line": "Fecha de referencia de los datos: 18 de junio de 2026",
    }

    assert TurnResponse(**base)
    with pytest.raises(ValidationError):
        TurnResponse(**base, contract_version="99")
    with pytest.raises(ValidationError, match="numbered from 1"):
        TurnResponse(**base, choices=(Choice(number=2, label="a"), Choice(number=2, label="b")))
    with pytest.raises(ValidationError):
        ReadinessPayload(
            status="ready",
            service_version="0.1.0",
            environment="prod",
            reference_date=_DOMAIN_DATE,
            reference_date_origin=ReferenceDateOrigin.SEED,
            policy_version="2",
            policy_digest="not-a-digest",
        )


# -----------------------------------------------------------------------------
# Closed sets only grow
# -----------------------------------------------------------------------------

# Every value a closed set held when the contract was frozen. A later version may add values to a
# set, never rename or remove one: these pins fail on any rename or removal.
_FROZEN_VALUES: dict[type[StrEnum], set[str]] = {
    Intent: {
        "clarify",
        "present_transactions",
        "confirm_filing",
        "filing_result",
        "ineligible",
        "dispute_status",
        "policy_answer",
        "abstain",
        "refuse",
        "handoff",
        "farewell",
    },
    Slot: {"transaction", "transaction_choice", "reason", "confirmation"},
    CustomerReason: {
        "eligible",
        "window_expired",
        "pending",
        "declined",
        "reversed",
        "duplicate_case",
        "not_disputable",
        "needs_review",
    },
    DateSource: {"absolute", "relative", "partial", "numeric"},
    NluIntent: {
        "file_dispute",
        "list_transactions",
        "dispute_status",
        "policy_question",
        "confirmation",
        "choice",
        "correction",
        "report_fraud",
        "report_card_loss",
        "request_person",
        "request_reversal",
        "unsupported_action",
        "switch_language",
        "small_talk",
        "farewell",
        "unclear",
    },
    ConfirmationAnswer: {"yes", "yes_with_change", "ambiguous", "no"},
    HandoffTrigger: {
        "fraud_report",
        "card_loss",
        "customer_request",
        "amount_review",
        "repeat_complainer",
        "risk_score",
        "amount_unknown",
        "low_understanding",
        "tool_failure",
        "filing_unverified",
    },
    TicketStatus: {"open", "in_review", "resolved", "rejected"},
    # Values of the policy vocabulary the contracts depend on.
    Outcome: {"eligible", "ineligible", "escalate"},
    DisputeCategory: {
        "unrecognized_charge",
        "duplicate_charge",
        "wrong_amount",
        "service_not_received",
        "fraud_claim",
    },
    TransactionStatus: {"Approved", "Declined", "Pending", "Reversed"},
    ReasonCode: {
        "eligible",
        "product_out_of_scope",
        "transaction_type_not_disputable",
        "transaction_declined",
        "transaction_pending",
        "transaction_reversed",
        "transaction_date_in_future",
        "filing_window_expired",
        "duplicate_open_case",
        "escalate_fraud_claim",
        "escalate_low_nlu_confidence",
        "escalate_repeat_complainer",
        "escalate_amount_above_threshold",
        "escalate_amount_unknown",
        "escalate_risk_score",
    },
}


@pytest.mark.parametrize("enumeration", list(_FROZEN_VALUES), ids=lambda e: e.__name__)
def test_a_closed_set_keeps_every_value_it_was_frozen_with(enumeration: type[StrEnum]) -> None:
    """A rename or a removal in a frozen closed set fails; an addition does not."""
    assert _FROZEN_VALUES[enumeration] <= {member.value for member in enumeration}


# -----------------------------------------------------------------------------
# Understanding: a slot exists exactly for its own intent
# -----------------------------------------------------------------------------


def test_confirmation_choice_and_requested_language_are_required_by_their_own_intent() -> None:
    """The confirmation, choice and switch_language intents do not arrive without their slot."""
    with pytest.raises(ValidationError, match="confirmation is read exactly"):
        NluResult(intent=NluIntent.CONFIRMATION, confidence=0.9)
    with pytest.raises(ValidationError, match="choice is read exactly"):
        NluResult(intent=NluIntent.CHOICE, confidence=0.9)
    with pytest.raises(ValidationError, match="requested_language is read exactly"):
        NluResult(intent=NluIntent.SWITCH_LANGUAGE, confidence=0.9)

    assert NluResult(intent=NluIntent.CHOICE, confidence=0.9, choice=2)


def test_a_transaction_hint_date_and_its_source_are_given_together() -> None:
    """A resolved date without how it was expressed, or the reverse, is refused."""
    with pytest.raises(ValidationError, match="given together"):
        TransactionHint(date_on=date(2026, 6, 17))
    with pytest.raises(ValidationError, match="given together"):
        TransactionHint(date_source=DateSource.RELATIVE)

    assert TransactionHint(date_on=date(2026, 6, 17), date_source=DateSource.RELATIVE)
