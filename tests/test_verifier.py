"""
Output Verifier Tests
======================

Component: ``app.conversation.verifier``. Hermetic and pure: verification reads only the envelope,
candidate and slot values passed in.

Every failure mode is seeded directly, per the E7 acceptance criterion that the verifier blocks a
seeded inconsistent response in tests — none of these needs a real model renderer (slice 2.5,
which does not exist yet and ships disabled until this slice merges, per D76).
"""

from __future__ import annotations

# Standard libraries
from datetime import date  # Fixed reference and case dates

# Third-party libraries
import pytest  # Parametrized numeral cases

# Local modules
from app.conversation.verifier import verify
from app.domain.policy.models import Outcome
from contracts.service_v1.envelope import (
    CaseFact,
    CustomerReason,
    Decision,
    DisputeFacts,
    GroundedField,
    Intent,
    RenderEnvelope,
)
from contracts.service_v1.verification import CandidateReply, RejectionReason, SlotValue, SlotValues

_DOMAIN_DATE = date(2026, 6, 18)


def _envelope(**changes: object) -> RenderEnvelope:
    values: dict[str, object] = {
        "session_id": "s-1",
        "lang": "en",
        "domain_date": _DOMAIN_DATE,
        "intent": Intent.INELIGIBLE,
        "render_mode": "model",
        "decisions": (
            Decision(
                outcome=Outcome.INELIGIBLE,
                customer_reason=CustomerReason.WINDOW_EXPIRED,
                policy_version="2",
            ),
        ),
    }
    return RenderEnvelope(**{**values, **changes})


def _slots(*pairs: tuple[GroundedField, str]) -> SlotValues:
    return SlotValues(entries=tuple(SlotValue(field=field, value=value) for field, value in pairs))


def _case(number: str, filed_on: date, transaction_ref: str) -> CaseFact:
    return CaseFact(
        case_number=number, status="Open", filed_on=filed_on, transaction_ref=transaction_ref
    )


# -----------------------------------------------------------------------------
# Accepted
# -----------------------------------------------------------------------------


def test_a_grounded_reply_is_accepted_and_substituted() -> None:
    """Every placeholder resolves to its declared field's grounded value."""
    envelope = _envelope()
    candidate = CandidateReply(raw_text="I'm sorry, {{outcome_statement}}")
    slots = _slots(
        (GroundedField.OUTCOME_STATEMENT, "the filing window for this charge has passed")
    )

    result = verify(envelope, candidate, slots)

    assert result.outcome == "accepted"
    assert result.rendered_text == "I'm sorry, the filing window for this charge has passed"
    assert result.reasons == ()


def test_a_field_cited_twice_consumes_its_entries_in_order() -> None:
    """A reply that lists two cases pairs each placeholder with the next case in order."""
    facts = DisputeFacts(
        cases=(
            _case("D-1", date(2026, 6, 1), "tx-1"),
            _case("D-2", date(2026, 6, 5), "tx-2"),
        )
    )
    envelope = _envelope(intent=Intent.DISPUTE_STATUS, facts=facts, decisions=())
    candidate = CandidateReply(
        raw_text="Case {{case_number}} filed {{filed_on}}; case {{case_number}} filed {{filed_on}}."
    )
    slots = _slots(
        (GroundedField.CASE_NUMBER, "D-1"),
        (GroundedField.FILED_ON, "June 1, 2026"),
        (GroundedField.CASE_NUMBER, "D-2"),
        (GroundedField.FILED_ON, "June 5, 2026"),
    )

    result = verify(envelope, candidate, slots)

    assert result.outcome == "accepted"
    assert result.rendered_text == "Case D-1 filed June 1, 2026; case D-2 filed June 5, 2026."


# -----------------------------------------------------------------------------
# Rejected: a digit the model wrote itself
# -----------------------------------------------------------------------------


def test_a_literal_digit_outside_any_slot_is_rejected() -> None:
    """A digit the model wrote on its own rejects the reply, whatever else the text says."""
    envelope = _envelope()
    candidate = CandidateReply(raw_text="Sorry, the deadline was 30 days ago.")

    result = verify(envelope, candidate, _slots())

    assert result.outcome == "rejected"
    assert RejectionReason.DIGIT_OUTSIDE_SLOT in result.reasons


@pytest.mark.parametrize("numeral", ["Ⅻ", "½", "⑩"], ids=["roman_numeral", "fraction", "circled"])
def test_a_non_ascii_numeral_character_is_rejected_like_a_digit(numeral: str) -> None:
    """A vulgar fraction, a circled digit or a single-character Roman numeral is a numeral
    character even though ``str.isdigit()`` does not recognize it; the check must catch it too."""
    envelope = _envelope()
    candidate = CandidateReply(raw_text=f"You owe about {numeral} of the claim.")

    result = verify(envelope, candidate, _slots())

    assert result.outcome == "rejected"
    assert RejectionReason.DIGIT_OUTSIDE_SLOT in result.reasons


def test_a_roman_numeral_spelled_in_plain_letters_is_a_known_limitation() -> None:
    """A quantity spelled entirely in ordinary letters contains no character Unicode itself
    classifies as numeric, so this check alone does not catch it (documented limitation)."""
    envelope = _envelope()
    candidate = CandidateReply(raw_text="You owe about MCMXCIV of the claim.")

    result = verify(envelope, candidate, _slots())

    assert RejectionReason.DIGIT_OUTSIDE_SLOT not in result.reasons


def test_a_fabricated_amount_built_from_grounded_digit_fragments_is_rejected() -> None:
    """A number built by concatenating pieces of two otherwise-grounded values cannot even reach
    a pattern check here: any digit the model writes rejects the reply outright, so there is no
    finished sentence left to inspect for a suspicious-looking number."""
    envelope = _envelope()
    candidate = CandidateReply(
        raw_text="Good news: you are getting a refund of 19,234.56 USD today."
    )

    result = verify(envelope, candidate, _slots((GroundedField.OUTCOME_STATEMENT, "234.56 USD")))

    assert result.outcome == "rejected"
    assert RejectionReason.DIGIT_OUTSIDE_SLOT in result.reasons


# -----------------------------------------------------------------------------
# Rejected: a field the model has no business naming
# -----------------------------------------------------------------------------


def test_an_undeclared_field_name_is_rejected() -> None:
    """A placeholder naming something outside ``GroundedField`` is rejected, not passed through."""
    envelope = _envelope()
    candidate = CandidateReply(raw_text="See {{document_number}} for details.")

    result = verify(envelope, candidate, _slots())

    assert result.outcome == "rejected"
    assert RejectionReason.UNDECLARED_PLACEHOLDER in result.reasons


def test_a_field_the_intent_does_not_allow_is_rejected() -> None:
    """A field that exists but does not belong to this intent is rejected the same way.

    This is the cross-customer/decision-consistency guarantee in practice: an ``ineligible``
    reply has no route to name an ``amount`` or a ``case_number`` at all, so a value that belongs
    to a different decision or a different customer's case has nowhere to enter even if the
    model tries to name it by field."""
    envelope = _envelope()
    candidate = CandidateReply(raw_text="Your case {{case_number}} is closed.")
    slots = _slots((GroundedField.CASE_NUMBER, "D-9"))

    result = verify(envelope, candidate, slots)

    assert result.outcome == "rejected"
    assert RejectionReason.UNDECLARED_PLACEHOLDER in result.reasons


def test_malformed_brace_syntax_is_rejected() -> None:
    """An unmatched or malformed brace is never treated as ordinary text."""
    envelope = _envelope()

    result = verify(envelope, CandidateReply(raw_text="See {{outcome_statement}"), _slots())

    assert result.outcome == "rejected"
    assert RejectionReason.UNDECLARED_PLACEHOLDER in result.reasons


# -----------------------------------------------------------------------------
# Rejected: a placeholder with nothing grounded to fill it
# -----------------------------------------------------------------------------


def test_a_placeholder_with_no_matching_slot_value_is_rejected() -> None:
    """A field the intent allows but the caller never grounded is rejected, not left blank."""
    envelope = _envelope()
    candidate = CandidateReply(raw_text="{{outcome_statement}}")

    result = verify(envelope, candidate, _slots())

    assert result.outcome == "rejected"
    assert RejectionReason.UNRESOLVED_PLACEHOLDER in result.reasons


def test_a_third_occurrence_beyond_the_entries_available_is_rejected() -> None:
    """Two grounded cases ground exactly two placeholder occurrences, not a third."""
    facts = DisputeFacts(
        cases=(
            _case("D-1", date(2026, 6, 1), "tx-1"),
            _case("D-2", date(2026, 6, 5), "tx-2"),
        )
    )
    envelope = _envelope(intent=Intent.DISPUTE_STATUS, facts=facts, decisions=())
    candidate = CandidateReply(
        raw_text="Case {{case_number}}, case {{case_number}}, case {{case_number}}."
    )
    slots = _slots((GroundedField.CASE_NUMBER, "D-1"), (GroundedField.CASE_NUMBER, "D-2"))

    result = verify(envelope, candidate, slots)

    assert result.outcome == "rejected"
    assert RejectionReason.UNRESOLVED_PLACEHOLDER in result.reasons


# -----------------------------------------------------------------------------
# Rejected: the outcome silently dropped
# -----------------------------------------------------------------------------


def test_omitting_a_required_field_is_rejected() -> None:
    """The model cannot silently drop the outcome an intent commits to stating."""
    envelope = _envelope()
    candidate = CandidateReply(raw_text="Thanks for reaching out.")

    result = verify(envelope, candidate, _slots())

    assert result.outcome == "rejected"
    assert RejectionReason.REQUIRED_FIELD_MISSING in result.reasons


def test_every_reason_that_applies_is_named_at_once() -> None:
    """A reply that fails more than one way names every reason, not just the first found."""
    envelope = _envelope()
    candidate = CandidateReply(raw_text="It has been 30 days.")

    result = verify(envelope, candidate, _slots())

    assert result.outcome == "rejected"
    assert RejectionReason.DIGIT_OUTSIDE_SLOT in result.reasons
    assert RejectionReason.REQUIRED_FIELD_MISSING in result.reasons
