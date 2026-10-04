"""
Template Renderer Tests
=======================

Component: ``app.conversation.renderer``. Hermetic and pure: rendering reads only the envelope
passed in, never a clock, a file or a network call.
"""

from __future__ import annotations

# Standard libraries
from datetime import date  # Fixed reference and transaction dates
from decimal import Decimal  # Money in the tests

# Third-party libraries
import pytest  # Test runner and parametrisation

# Local modules
from app.conversation.renderer import (
    demo_notice,
    format_date,
    format_money,
    reference_date_line,
    render,
)
from app.domain.policy.models import DisputeCategory, Outcome, TransactionStatus
from contracts.service_v1.envelope import (
    CaseFact,
    CustomerReason,
    Decision,
    DisputeFacts,
    Envelope,
    Intent,
    Lang,
    LocalizedTitle,
    Money,
    PolicyValue,
    ProductLabel,
    RenderEnvelope,
    Slot,
    SourceRef,
    TemplateId,
    TransactionFact,
    WindowFact,
)
from tests.fixtures import scripted_flows

_DOMAIN_DATE = date(2026, 6, 18)


def _envelope(**changes: object) -> RenderEnvelope:
    values: dict[str, object] = {
        "session_id": "s-1",
        "lang": "es",
        "domain_date": _DOMAIN_DATE,
        "intent": Intent.CLARIFY,
        "template_id": TemplateId.GREETING,
    }
    return RenderEnvelope(**{**values, **changes})


def _transaction(**changes: object) -> TransactionFact:
    values: dict[str, object] = {
        "ref": "tx-1001",
        "occurred_on": date(2026, 6, 12),
        "merchant": "Tienda Sol",
        "amount": Money(amount=Decimal("250.00"), currency="MXN"),
        "product": ProductLabel(name="Visa Classic", last4="4321"),
        "status": TransactionStatus.APPROVED,
    }
    return TransactionFact(**{**values, **changes})


def _source() -> SourceRef:
    return SourceRef(
        section_id="filing-windows",
        titles=(
            LocalizedTitle(lang="es", text="Plazos para disputar"),
            LocalizedTitle(lang="pt", text="Prazos para contestar"),
            LocalizedTitle(lang="en", text="Filing windows"),
        ),
        corpus_version="2",
    )


# -----------------------------------------------------------------------------
# Dates, amounts and the persistent line
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("es", "18 de junio de 2026"),
        ("pt", "18 de junho de 2026"),
        ("en", "June 18, 2026"),
    ],
)
def test_format_date_is_absolute_and_carries_the_year(lang: str, expected: str) -> None:
    """Every date is written in words with the year, in the reply language."""
    assert format_date(_DOMAIN_DATE, lang) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("es", "Fecha de referencia de los datos: 18 de junio de 2026"),
        ("pt", "Data de referência dos dados: 18 de junho de 2026"),
        ("en", "Reference date of the data: June 18, 2026"),
    ],
)
def test_reference_date_line_is_written_in_the_reply_language(lang: str, expected: str) -> None:
    """The reference-date line uses the exact wording for each reply language."""
    assert reference_date_line(_DOMAIN_DATE, lang) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("en", "5,000.00 USD"),
        ("es", f"5{chr(0xA0)}000,00 USD"),
        ("pt", "5.000,00 USD"),
    ],
)
def test_format_money_uses_each_language_s_separators(lang: str, expected: str) -> None:
    """The amount always carries its currency code, in the reply language's convention."""
    money = Money(amount=Decimal("5000.00"), currency="USD")

    assert format_money(money, lang) == expected  # type: ignore[arg-type]


def test_demo_notice_is_stated_in_every_language() -> None:
    """The demonstration notice exists in all three reply languages."""
    for lang in ("es", "pt", "en"):
        assert "sintéticos" in demo_notice(lang) or "synthetic" in demo_notice(lang)


# -----------------------------------------------------------------------------
# Every template renders, in every language, without stating an ungrounded number
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["es", "pt", "en"])
def test_every_scripted_fixture_step_renders_without_an_ungrounded_number(lang: str) -> None:
    """The recorded conversations render cleanly end to end."""
    flows = scripted_flows.flows(lang)  # type: ignore[arg-type]
    for steps in flows.values():
        for step in steps:
            envelope = Envelope.model_validate(step).render_view()
            rendered = render(envelope)
            assert rendered.reply
            assert rendered.reference_date_line == reference_date_line(
                envelope.domain_date, envelope.lang
            )


def _every_template_envelope(lang: str) -> dict[TemplateId, RenderEnvelope]:
    """One valid envelope per ``TemplateId``, enough to exercise every renderer function."""
    transaction = _transaction()
    shown = DisputeFacts(transactions=(transaction,), candidate_count=1)
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
    case = CaseFact(
        case_number="D-2001",
        status="Open",
        filed_on=_DOMAIN_DATE,
        transaction_ref=transaction.ref,
        expected_response_on=date(2026, 6, 25),
    )
    common = {"session_id": "s-1", "lang": lang, "domain_date": _DOMAIN_DATE}

    def env(**changes: object) -> RenderEnvelope:
        return RenderEnvelope(**{**common, **changes})

    return {
        TemplateId.GREETING: env(intent=Intent.CLARIFY, template_id=TemplateId.GREETING),
        TemplateId.CLARIFY_TRANSACTION: env(
            intent=Intent.CLARIFY, template_id=TemplateId.CLARIFY_TRANSACTION
        ),
        TemplateId.CLARIFY_REASON: env(
            intent=Intent.CLARIFY, template_id=TemplateId.CLARIFY_REASON, facts=shown
        ),
        TemplateId.CLARIFY_CHOICE: env(
            intent=Intent.CLARIFY, template_id=TemplateId.CLARIFY_CHOICE
        ),
        TemplateId.CLARIFY_CONFIRMATION: env(
            intent=Intent.CLARIFY, template_id=TemplateId.CLARIFY_CONFIRMATION
        ),
        TemplateId.LANGUAGE_OFFER: env(
            intent=Intent.CLARIFY, template_id=TemplateId.LANGUAGE_OFFER
        ),
        TemplateId.PRESENT_ONE: env(
            intent=Intent.PRESENT_TRANSACTIONS, template_id=TemplateId.PRESENT_ONE, facts=shown
        ),
        TemplateId.PRESENT_LIST: env(
            intent=Intent.PRESENT_TRANSACTIONS,
            template_id=TemplateId.PRESENT_LIST,
            facts=DisputeFacts(transactions=(transaction,), candidate_count=3),
        ),
        TemplateId.PRESENT_NARROW: env(
            intent=Intent.CLARIFY, template_id=TemplateId.PRESENT_NARROW
        ),
        TemplateId.NOT_FOUND: env(intent=Intent.CLARIFY, template_id=TemplateId.NOT_FOUND),
        TemplateId.CONFIRM_FILING: env(
            intent=Intent.CONFIRM_FILING,
            template_id=TemplateId.CONFIRM_FILING,
            next_expected=Slot.CONFIRMATION,
            facts=DisputeFacts(
                transactions=(transaction,),
                candidate_count=1,
                selected_ref=transaction.ref,
                category=DisputeCategory.UNRECOGNIZED_CHARGE,
            ),
            decisions=(eligible,),
        ),
        TemplateId.FILING_RESULT: env(
            intent=Intent.FILING_RESULT,
            template_id=TemplateId.FILING_RESULT,
            facts=DisputeFacts(cases=(case,)),
        ),
        TemplateId.FILING_UNVERIFIED: env(
            intent=Intent.HANDOFF,
            end_session=True,
            template_id=TemplateId.FILING_UNVERIFIED,
            facts=DisputeFacts(ticket_ref="T-1"),
        ),
        TemplateId.FILING_CANCELLED: env(
            intent=Intent.CLARIFY, template_id=TemplateId.FILING_CANCELLED
        ),
        TemplateId.INELIGIBLE: env(
            intent=Intent.INELIGIBLE,
            template_id=TemplateId.INELIGIBLE,
            facts=DisputeFacts(
                window=WindowFact(days_allowed=60, age_days=137, deadline=date(2026, 4, 2))
            ),
            decisions=(ineligible,),
        ),
        TemplateId.DISPUTE_STATUS: env(
            intent=Intent.DISPUTE_STATUS,
            template_id=TemplateId.DISPUTE_STATUS,
            facts=DisputeFacts(cases=(case,)),
        ),
        TemplateId.NO_CASE_FOUND: env(
            intent=Intent.DISPUTE_STATUS, template_id=TemplateId.NO_CASE_FOUND
        ),
        TemplateId.POLICY_ANSWER: env(
            intent=Intent.POLICY_ANSWER,
            template_id=TemplateId.POLICY_ANSWER,
            facts=DisputeFacts(policy_values=(PolicyValue(name="filing_window_days", value="60"),)),
            sources=(_source(),),
        ),
        TemplateId.ABSTAIN_POLICY: env(
            intent=Intent.ABSTAIN, template_id=TemplateId.ABSTAIN_POLICY
        ),
        TemplateId.REFUSE_UNSUPPORTED: env(
            intent=Intent.REFUSE, template_id=TemplateId.REFUSE_UNSUPPORTED
        ),
        TemplateId.REFUSE_REVERSAL: env(
            intent=Intent.REFUSE, template_id=TemplateId.REFUSE_REVERSAL
        ),
        TemplateId.HANDOFF_REVIEW: env(
            intent=Intent.HANDOFF,
            end_session=True,
            template_id=TemplateId.HANDOFF_REVIEW,
            facts=DisputeFacts(ticket_ref="T-100", contact_within_hours=24),
            decisions=(
                Decision(
                    outcome=Outcome.ESCALATE,
                    customer_reason=CustomerReason.NEEDS_REVIEW,
                    policy_version="2",
                ),
            ),
        ),
        TemplateId.HANDOFF_FRAUD: env(
            intent=Intent.HANDOFF,
            end_session=True,
            template_id=TemplateId.HANDOFF_FRAUD,
            facts=DisputeFacts(ticket_ref="T-101", contact_within_hours=24),
            decisions=(
                Decision(
                    outcome=Outcome.ESCALATE,
                    customer_reason=CustomerReason.NEEDS_REVIEW,
                    policy_version="2",
                ),
            ),
        ),
        TemplateId.HANDOFF_CARD_LOSS: env(
            intent=Intent.HANDOFF,
            end_session=True,
            template_id=TemplateId.HANDOFF_CARD_LOSS,
            facts=DisputeFacts(ticket_ref="T-102"),
        ),
        TemplateId.HANDOFF_REQUESTED: env(
            intent=Intent.HANDOFF,
            end_session=True,
            template_id=TemplateId.HANDOFF_REQUESTED,
            facts=DisputeFacts(ticket_ref="T-103"),
        ),
        TemplateId.HANDOFF_NOT_REGISTERED: env(
            intent=Intent.HANDOFF,
            end_session=True,
            template_id=TemplateId.HANDOFF_NOT_REGISTERED,
            facts=DisputeFacts(),
        ),
        TemplateId.RESTART_AFTER_PENDING: env(
            intent=Intent.CLARIFY, template_id=TemplateId.RESTART_AFTER_PENDING
        ),
        TemplateId.FAREWELL: env(
            intent=Intent.FAREWELL, end_session=True, template_id=TemplateId.FAREWELL
        ),
    }


@pytest.mark.parametrize("lang", ["es", "pt", "en"])
def test_every_template_id_has_a_working_renderer(lang: str) -> None:
    """Every value of the closed set renders to nonempty text with no ungrounded number."""
    envelopes = _every_template_envelope(lang)

    assert set(envelopes) == set(TemplateId)
    for envelope in envelopes.values():
        rendered = render(envelope)
        assert rendered.reply.strip()


def test_the_spanish_language_offer_does_not_speak_in_a_gendered_first_person() -> None:
    """The assistant has no gender, so the Spanish offer must not say it is "segura"."""
    envelope = _envelope(template_id=TemplateId.LANGUAGE_OFFER)

    reply = render(envelope).reply

    assert "No sé si prefiere continuar en español o portugués" in reply
    assert "segura" not in reply.lower()


@pytest.mark.parametrize("lang", ["es", "pt"])
def test_the_spanish_and_portuguese_language_offers_tell_an_english_customer_they_can_switch(
    lang: str,
) -> None:
    reply = render(_envelope(template_id=TemplateId.LANGUAGE_OFFER, lang=lang)).reply

    assert "let me know if you prefer English" in reply


def test_portuguese_replies_keep_their_fixed_wording_faults_out() -> None:
    """No uncontracted "em a"/"por a", no sentence-initial clitic, no "a um atendente"."""
    envelopes = _every_template_envelope("pt")

    for template_id, envelope in envelopes.items():
        reply = render(envelope).reply
        for fragment in ("em a ", "por a ", "Conte-me", "avise-me", " a um atendente"):
            assert fragment not in reply, (template_id, fragment)


def test_render_refuses_a_non_template_envelope() -> None:
    """Model-mode envelopes are the model renderer's job, not this one's."""
    envelope = RenderEnvelope(
        session_id="s-1",
        lang="es",
        domain_date=_DOMAIN_DATE,
        intent=Intent.CLARIFY,
        render_mode="model",
    )

    with pytest.raises(ValueError, match="template-mode"):
        render(envelope)


def test_a_transaction_without_a_merchant_is_shown_without_inventing_one() -> None:
    """A null merchant is never filled in with an invented name."""
    envelope = _envelope(
        intent=Intent.PRESENT_TRANSACTIONS,
        template_id=TemplateId.PRESENT_ONE,
        facts=DisputeFacts(transactions=(_transaction(merchant=None),), candidate_count=1),
    )

    rendered = render(envelope)

    assert "None" not in rendered.reply


@pytest.mark.parametrize(
    ("lang", "phrase"),
    [
        ("es", "monto no disponible"),
        ("pt", "valor não disponível"),
        ("en", "amount that isn't available"),
    ],
)
def test_a_transaction_without_an_amount_states_it_plainly_in_every_language(
    lang: Lang, phrase: str
) -> None:
    """A figure the source never gave is stated as absent, never invented, in each language."""
    envelope = _envelope(
        intent=Intent.PRESENT_TRANSACTIONS,
        template_id=TemplateId.PRESENT_ONE,
        lang=lang,
        facts=DisputeFacts(transactions=(_transaction(amount=None),), candidate_count=1),
    )

    rendered = render(envelope)

    assert "None" not in rendered.reply
    assert phrase in rendered.reply


@pytest.mark.parametrize(
    ("lang", "expected_phrase"),
    [
        ("es", "en Tienda Sol"),
        ("pt", "em Tienda Sol no dia 12 de junho de 2026"),
        ("en", "at Tienda Sol"),
    ],
)
def test_present_one_names_the_merchant_with_its_own_language_s_preposition(
    lang: Lang, expected_phrase: str
) -> None:
    """The merchant name never carries a preposition borrowed from another reply language."""
    envelope = _envelope(
        intent=Intent.PRESENT_TRANSACTIONS,
        template_id=TemplateId.PRESENT_ONE,
        lang=lang,
        facts=DisputeFacts(transactions=(_transaction(),), candidate_count=1),
    )

    rendered = render(envelope)

    assert expected_phrase in rendered.reply


@pytest.mark.parametrize(
    ("category", "expected_phrase"),
    [
        (DisputeCategory.UNRECOGNIZED_CHARGE, "an unrecognized charge dispute"),
        (DisputeCategory.DUPLICATE_CHARGE, "a duplicate charge dispute"),
        (DisputeCategory.WRONG_AMOUNT, "a wrong amount dispute"),
        (DisputeCategory.SERVICE_NOT_RECEIVED, "a service not received dispute"),
        (DisputeCategory.FRAUD_CLAIM, "a fraud claim dispute"),
    ],
)
def test_confirm_filing_uses_the_grammatical_article_in_english(
    category: DisputeCategory, expected_phrase: str
) -> None:
    """ "an" precedes a vowel-sound category name in English; "a" precedes every other one."""
    facts = DisputeFacts(
        transactions=(_transaction(),),
        candidate_count=1,
        selected_ref="tx-1001",
        category=category,
    )
    envelope = _envelope(
        lang="en",
        intent=Intent.CONFIRM_FILING,
        template_id=TemplateId.CONFIRM_FILING,
        facts=facts,
        decisions=(
            Decision(
                outcome=Outcome.ELIGIBLE,
                customer_reason=CustomerReason.ELIGIBLE,
                policy_version="2",
                requires_confirmation=True,
            ),
        ),
    )

    rendered = render(envelope)

    assert expected_phrase in rendered.reply


def test_confirm_filing_states_a_missing_amount_plainly_too() -> None:
    """The confirmation prompt is grounded the same way present_transactions is."""
    facts = DisputeFacts(
        transactions=(_transaction(amount=None),),
        candidate_count=1,
        selected_ref="tx-1001",
        category=DisputeCategory.UNRECOGNIZED_CHARGE,
    )
    envelope = _envelope(
        intent=Intent.CONFIRM_FILING,
        template_id=TemplateId.CONFIRM_FILING,
        facts=facts,
        decisions=(
            Decision(
                outcome=Outcome.ELIGIBLE,
                customer_reason=CustomerReason.ELIGIBLE,
                policy_version="2",
                requires_confirmation=True,
            ),
        ),
    )

    rendered = render(envelope)

    assert "None" not in rendered.reply


@pytest.mark.parametrize(
    ("reason", "keyword"),
    [
        (CustomerReason.WINDOW_EXPIRED, "venció"),
        (CustomerReason.PENDING, "pendiente"),
        (CustomerReason.DECLINED, "rechazada"),
        (CustomerReason.REVERSED, "revertida"),
        (CustomerReason.DUPLICATE_CASE, "abierta"),
        (CustomerReason.NOT_DISPUTABLE, "no se puede"),
    ],
)
def test_every_ineligible_reason_has_its_own_plain_wording(
    reason: CustomerReason, keyword: str
) -> None:
    """Each of the six refusal reasons reads distinctly, in the customer's own words."""
    envelope = _envelope(
        intent=Intent.INELIGIBLE,
        template_id=TemplateId.INELIGIBLE,
        decisions=(
            Decision(outcome=Outcome.INELIGIBLE, customer_reason=reason, policy_version="2"),
        ),
    )

    rendered = render(envelope)

    assert keyword in rendered.reply.lower()


def test_the_policy_answer_cites_the_section_title_in_the_reply_language() -> None:
    """The reply names the source by its readable title, not its identifier."""
    source = _source()
    envelope = _envelope(
        intent=Intent.POLICY_ANSWER,
        template_id=TemplateId.POLICY_ANSWER,
        lang="pt",
        facts=DisputeFacts(policy_values=(PolicyValue(name="filing_window_days", value="60"),)),
        sources=(source,),
    )

    rendered = render(envelope)

    assert source.title_for("pt") in rendered.reply
    assert source.section_id not in rendered.reply


def test_dispute_status_grounds_a_case_without_an_expected_response_date() -> None:
    """A case fact with no response date yet still renders without failing."""
    case = CaseFact(
        case_number="D-9", status="Open", filed_on=_DOMAIN_DATE, transaction_ref="tx-1001"
    )
    envelope = _envelope(
        intent=Intent.DISPUTE_STATUS,
        template_id=TemplateId.DISPUTE_STATUS,
        facts=DisputeFacts(cases=(case,)),
    )

    rendered = render(envelope)

    assert case.case_number in rendered.reply
