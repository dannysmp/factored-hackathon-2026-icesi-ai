"""
Slot Values Tests
==================

Component: ``app.conversation.slot_values.slot_values_for``. Hermetic: pure function, no I/O.
"""

from __future__ import annotations

# Standard libraries
from datetime import date
from decimal import Decimal

# Third-party libraries
import pytest  # Parametrisation

# Local modules
from app.conversation.renderer import CASE_STATUS_NAMES, INELIGIBLE_TEXT
from app.conversation.slot_values import slot_values_for
from app.domain.policy.models import DisputeCategory, Outcome, TransactionStatus
from contracts.service_v1.cases import CaseStatus
from contracts.service_v1.envelope import (
    INTENT_REQUIRED_FIELDS,
    CaseFact,
    CustomerReason,
    Decision,
    DisputeFacts,
    GroundedField,
    Intent,
    Lang,
    LocalizedTitle,
    Money,
    PolicyValue,
    ProductLabel,
    RenderEnvelope,
    SourceRef,
    TemplateId,
    TransactionFact,
)
from contracts.service_v1.verification import SlotValue

_DOMAIN_DATE = date(2026, 6, 18)


def _envelope(**overrides: object) -> RenderEnvelope:
    values: dict[str, object] = {
        "session_id": "s1",
        "lang": "es",
        "domain_date": _DOMAIN_DATE,
        "intent": Intent.FAREWELL,
        "render_mode": "model",
    }
    values.update(overrides)
    return RenderEnvelope(**values)


def test_present_transactions_omits_merchant_when_the_source_gives_none() -> None:
    fact = TransactionFact(
        ref="tx-1",
        occurred_on=date(2026, 6, 1),
        merchant=None,
        amount=Money(amount=Decimal("10.00"), currency="MXN"),
        product=ProductLabel(name="Visa", last4="1234"),
        status=TransactionStatus.APPROVED,
    )
    envelope = _envelope(
        intent=Intent.PRESENT_TRANSACTIONS,
        facts=DisputeFacts(transactions=(fact,), candidate_count=1),
    )

    values = slot_values_for(envelope)

    fields = [entry.field for entry in values.entries]
    assert GroundedField.MERCHANT not in fields
    assert GroundedField.AMOUNT in fields
    assert GroundedField.OCCURRED_ON in fields


def test_present_transactions_lists_one_amount_entry_per_transaction() -> None:
    fact = TransactionFact(
        ref="tx-1",
        occurred_on=date(2026, 6, 1),
        merchant="Tienda",
        amount=None,
        product=ProductLabel(name="Visa", last4="1234"),
        status=TransactionStatus.APPROVED,
    )
    envelope = _envelope(
        intent=Intent.PRESENT_TRANSACTIONS,
        facts=DisputeFacts(transactions=(fact, fact), candidate_count=2),
    )

    values = slot_values_for(envelope)

    amounts = [entry for entry in values.entries if entry.field is GroundedField.AMOUNT]
    assert len(amounts) == 2
    assert all("no disponible" in entry.value for entry in amounts)


def test_dispute_status_lists_one_entry_set_per_case() -> None:
    cases = tuple(
        CaseFact(case_number=f"D-{i}", status="Open", filed_on=_DOMAIN_DATE, transaction_ref="tx-1")
        for i in range(3)
    )
    envelope = _envelope(intent=Intent.DISPUTE_STATUS, facts=DisputeFacts(cases=cases))

    values = slot_values_for(envelope)

    case_numbers = [
        entry.value for entry in values.entries if entry.field is GroundedField.CASE_NUMBER
    ]
    assert case_numbers == ["D-0", "D-1", "D-2"]
    assert GroundedField.OUTCOME_STATEMENT in {entry.field for entry in values.entries}


def test_dispute_status_states_the_outcome_whether_or_not_a_case_exists() -> None:
    """The required field is present in both states, and the wording differs between them."""
    case = CaseFact(case_number="D-1", status="Open", filed_on=_DOMAIN_DATE, transaction_ref="tx-1")
    with_case = slot_values_for(
        _envelope(intent=Intent.DISPUTE_STATUS, facts=DisputeFacts(cases=(case,)))
    )
    without_case = slot_values_for(_envelope(intent=Intent.DISPUTE_STATUS, facts=DisputeFacts()))

    with_case_statement = next(
        e.value for e in with_case.entries if e.field is GroundedField.OUTCOME_STATEMENT
    )
    without_case_statement = next(
        e.value for e in without_case.entries if e.field is GroundedField.OUTCOME_STATEMENT
    )
    assert with_case_statement != without_case_statement


def test_filing_result_omits_expected_response_when_absent() -> None:
    case = CaseFact(case_number="D-1", status="Open", filed_on=_DOMAIN_DATE, transaction_ref="tx-1")
    envelope = _envelope(intent=Intent.FILING_RESULT, facts=DisputeFacts(cases=(case,)))

    values = slot_values_for(envelope)

    fields = [entry.field for entry in values.entries]
    assert GroundedField.EXPECTED_RESPONSE_ON not in fields
    assert GroundedField.CASE_NUMBER in fields


def test_policy_answer_with_no_values_still_cites_the_source() -> None:
    source = SourceRef(
        section_id="overview",
        corpus_version="1",
        titles=(
            LocalizedTitle(lang="es", text="Resumen"),
            LocalizedTitle(lang="pt", text="Resumo"),
            LocalizedTitle(lang="en", text="Overview"),
        ),
    )
    envelope = _envelope(intent=Intent.POLICY_ANSWER, sources=(source,))

    values = slot_values_for(envelope)

    assert values.entries == (SlotValue(field=GroundedField.SOURCE_TITLE, value="Resumen"),)


def test_policy_answer_lists_one_entry_per_value_with_no_name_prefix() -> None:
    source = SourceRef(
        section_id="overview",
        corpus_version="1",
        titles=(
            LocalizedTitle(lang="es", text="Resumen"),
            LocalizedTitle(lang="pt", text="Resumo"),
            LocalizedTitle(lang="en", text="Overview"),
        ),
    )
    envelope = _envelope(
        intent=Intent.POLICY_ANSWER,
        sources=(source,),
        facts=DisputeFacts(
            policy_values=(
                PolicyValue(name="filing_window_days", value="120"),
                PolicyValue(name="evidence_required", value="receipt, statement"),
            )
        ),
    )

    values = slot_values_for(envelope)

    policy_entries = [
        entry.value for entry in values.entries if entry.field is GroundedField.POLICY_VALUE
    ]
    assert policy_entries == ["120", "receipt, statement"]
    assert all("filing_window_days" not in entry for entry in policy_entries)


def test_handoff_omits_contact_within_hours_when_absent() -> None:
    envelope = _envelope(
        intent=Intent.HANDOFF,
        end_session=True,
        facts=DisputeFacts(ticket_ref="T-100"),
        decisions=(
            Decision(
                outcome=Outcome.ESCALATE,
                customer_reason=CustomerReason.NEEDS_REVIEW,
                policy_version="2",
            ),
        ),
    )

    values = slot_values_for(envelope)

    fields = {entry.field for entry in values.entries}
    assert GroundedField.OUTCOME_STATEMENT in fields
    assert GroundedField.TICKET_REF in fields
    assert GroundedField.CONTACT_WITHIN_HOURS not in fields


def test_ineligible_states_the_same_wording_the_template_renderer_uses() -> None:
    envelope = _envelope(
        intent=Intent.INELIGIBLE,
        decisions=(
            Decision(
                outcome=Outcome.INELIGIBLE,
                customer_reason=CustomerReason.WINDOW_EXPIRED,
                policy_version="2",
            ),
        ),
    )

    values = slot_values_for(envelope)

    assert values.entries[0].value == INELIGIBLE_TEXT["es"][CustomerReason.WINDOW_EXPIRED]


def test_confirm_filing_states_the_localized_category_name() -> None:
    fact = TransactionFact(
        ref="tx-1",
        occurred_on=date(2026, 6, 1),
        merchant="Tienda",
        amount=Money(amount=Decimal("10.00"), currency="MXN"),
        product=ProductLabel(name="Visa", last4="1234"),
        status=TransactionStatus.APPROVED,
    )
    envelope = _envelope(
        intent=Intent.CONFIRM_FILING,
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

    values = slot_values_for(envelope)

    category_entries = [
        entry.value for entry in values.entries if entry.field is GroundedField.CATEGORY
    ]
    assert category_entries == ["reporte de fraude"]


def test_procedural_intents_produce_no_entries() -> None:
    cases = (
        (Intent.CLARIFY, TemplateId.GREETING, False),
        (Intent.ABSTAIN, TemplateId.ABSTAIN_POLICY, False),
        (Intent.REFUSE, TemplateId.REFUSE_UNSUPPORTED, False),
        (Intent.FAREWELL, TemplateId.FAREWELL, True),
    )
    for intent, template_id, end_session in cases:
        envelope = _envelope(
            intent=intent,
            end_session=end_session,
            render_mode="template",
            template_id=template_id,
        )
        assert slot_values_for(envelope).entries == ()


def _buildable_states() -> list[RenderEnvelope]:
    """One envelope per intent's buildable state — two for the intents that have more than one."""
    transaction = TransactionFact(
        ref="tx-1",
        occurred_on=date(2026, 6, 1),
        merchant="Tienda",
        amount=Money(amount=Decimal("10.00"), currency="MXN"),
        product=ProductLabel(name="Visa", last4="1234"),
        status=TransactionStatus.APPROVED,
    )
    case = CaseFact(case_number="D-1", status="Open", filed_on=_DOMAIN_DATE, transaction_ref="tx-1")
    source = SourceRef(
        section_id="overview",
        corpus_version="1",
        titles=(
            LocalizedTitle(lang="es", text="Resumen"),
            LocalizedTitle(lang="pt", text="Resumo"),
            LocalizedTitle(lang="en", text="Overview"),
        ),
    )
    eligible = Decision(
        outcome=Outcome.ELIGIBLE,
        customer_reason=CustomerReason.ELIGIBLE,
        policy_version="2",
        requires_confirmation=True,
    )
    ineligible = Decision(
        outcome=Outcome.INELIGIBLE,
        customer_reason=CustomerReason.WINDOW_EXPIRED,
        policy_version="2",
    )
    escalate = Decision(
        outcome=Outcome.ESCALATE, customer_reason=CustomerReason.NEEDS_REVIEW, policy_version="2"
    )
    return [
        _envelope(intent=Intent.CLARIFY),
        _envelope(
            intent=Intent.PRESENT_TRANSACTIONS,
            facts=DisputeFacts(transactions=(transaction,), candidate_count=1),
        ),
        _envelope(
            intent=Intent.CONFIRM_FILING,
            facts=DisputeFacts(
                transactions=(transaction,),
                candidate_count=1,
                selected_ref="tx-1",
                category=DisputeCategory.FRAUD_CLAIM,
            ),
            decisions=(eligible,),
        ),
        _envelope(intent=Intent.FILING_RESULT, facts=DisputeFacts(cases=(case,))),
        _envelope(intent=Intent.INELIGIBLE, decisions=(ineligible,)),
        _envelope(intent=Intent.DISPUTE_STATUS, facts=DisputeFacts(cases=(case,))),
        _envelope(intent=Intent.DISPUTE_STATUS, facts=DisputeFacts()),
        _envelope(intent=Intent.POLICY_ANSWER, sources=(source,)),
        _envelope(intent=Intent.ABSTAIN),
        _envelope(
            intent=Intent.REFUSE, render_mode="template", template_id=TemplateId.REFUSE_UNSUPPORTED
        ),
        _envelope(
            intent=Intent.HANDOFF,
            end_session=True,
            facts=DisputeFacts(ticket_ref="T-100"),
            decisions=(escalate,),
        ),
        _envelope(intent=Intent.HANDOFF, end_session=True, facts=DisputeFacts(), decisions=()),
        _envelope(intent=Intent.FAREWELL, end_session=True),
    ]


def test_every_required_field_is_satisfiable_from_the_envelope_alone() -> None:
    """A field ``INTENT_REQUIRED_FIELDS`` names can never fail for want of a value to substitute,
    in every state the contract actually lets that intent build in — not just the states these
    other tests happen to exercise."""
    for envelope in _buildable_states():
        produced = {entry.field for entry in slot_values_for(envelope).entries}
        required = INTENT_REQUIRED_FIELDS[envelope.intent]
        assert required <= produced, f"{envelope.intent}: missing {required - produced}"


@pytest.mark.parametrize("lang", ["es", "pt", "en"])
@pytest.mark.parametrize("status", list(CaseStatus))
def test_dispute_status_names_each_case_status_in_the_reply_language(
    lang: Lang, status: CaseStatus
) -> None:
    case = CaseFact(
        case_number="D-1", status=status.value, filed_on=_DOMAIN_DATE, transaction_ref="tx-1"
    )
    envelope = _envelope(lang=lang, intent=Intent.DISPUTE_STATUS, facts=DisputeFacts(cases=(case,)))

    statuses = [
        entry.value
        for entry in slot_values_for(envelope).entries
        if entry.field is GroundedField.CASE_STATUS
    ]

    assert statuses == [CASE_STATUS_NAMES[lang][status]]
