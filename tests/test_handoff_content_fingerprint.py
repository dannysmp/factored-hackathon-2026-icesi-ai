"""
Handoff Content Fingerprint Tests
===================================

Component: ``app.persistence.handoff_outbox.content_fingerprint``. Hermetic and pure: no store, no
clock, no network call.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.conversation.handoff import HandoffContent

# Local modules
from app.domain.policy.models import ReasonCode
from app.persistence.handoff_outbox import content_fingerprint
from contracts.service_v1.handoff import HandoffTrigger

_REFERENCE_DATE = date(2026, 6, 18)
_CREATED_AT = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)


def _content(**changes: Any) -> HandoffContent:
    values: dict[str, Any] = {
        "reference_date": _REFERENCE_DATE,
        "created_at": _CREATED_AT,
        "language": "es",
        "trigger": HandoffTrigger.CUSTOMER_REQUEST,
        "first_name": "Ana",
        "customer_id": "CLI-1234",
        "request_summary": "Wants to speak with a person.",
        "reason_codes": (ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,),
        "policy_version": "2",
    }
    return HandoffContent(**{**values, **changes})


def test_the_same_content_produces_the_same_fingerprint() -> None:
    """Two equal-by-value but distinct instances digest identically: the fingerprint depends on
    the content, not on object identity."""
    assert content_fingerprint(_content()) == content_fingerprint(_content())


def test_changing_a_real_content_field_changes_the_fingerprint() -> None:
    baseline = _content()
    changed = _content(request_summary="A different request entirely.")

    assert content_fingerprint(baseline) != content_fingerprint(changed)


def test_reference_date_and_created_at_are_excluded_from_the_fingerprint() -> None:
    """A genuine retry's freshly-read clock and reference date must never look like a content
    disagreement — these are identity/timing fields, not the handoff itself."""
    baseline = _content()
    changed = _content(reference_date=date(2020, 1, 1), created_at=datetime(2020, 1, 1, tzinfo=UTC))

    assert content_fingerprint(baseline) == content_fingerprint(changed)


def test_customer_id_is_compared_through_its_masked_label() -> None:
    """The raw identifier is excluded; only its masked label affects the fingerprint, matching
    what the stored row itself keeps."""
    baseline = _content(customer_id="AAA-1234")
    same_last_four = _content(customer_id="ZZZ-1234")
    different_last_four = _content(customer_id="AAA-9999")

    assert content_fingerprint(baseline) == content_fingerprint(same_last_four)
    assert content_fingerprint(baseline) != content_fingerprint(different_last_four)


@dataclass(frozen=True, slots=True)
class _FutureHandoffContent:
    """A stand-in for a later version of ``HandoffContent`` with a field that does not exist on
    it today, written specifically to prove ``content_fingerprint`` needs no edit to cover one."""

    reference_date: date
    created_at: datetime
    customer_id: str
    a_field_handoffcontent_does_not_have_today: str


def test_a_field_the_fingerprint_has_never_seen_still_changes_the_digest() -> None:
    """The whole point of computing the fingerprint from ``dataclasses.fields`` rather than a
    hand-picked list: a field this function was never edited to know about is still covered,
    because it iterates whatever fields the object actually has."""
    baseline = _FutureHandoffContent(
        reference_date=_REFERENCE_DATE,
        created_at=_CREATED_AT,
        customer_id="CLI-1234",
        a_field_handoffcontent_does_not_have_today="original",
    )
    changed = replace(baseline, a_field_handoffcontent_does_not_have_today="changed")

    assert content_fingerprint(baseline) != content_fingerprint(changed)  # type: ignore[arg-type]


def test_an_excluded_field_name_is_skipped_on_any_dataclass_not_only_handoffcontent() -> None:
    """The exclusion is by field name, not by ``HandoffContent``'s own specific field types —
    proven here against a value type ``content_fingerprint`` was never written with in mind."""
    baseline = _FutureHandoffContent(
        reference_date=_REFERENCE_DATE,
        created_at=_CREATED_AT,
        customer_id="CLI-1234",
        a_field_handoffcontent_does_not_have_today="same",
    )
    changed = replace(baseline, reference_date=date(1999, 1, 1))

    assert content_fingerprint(baseline) == content_fingerprint(changed)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class _ContentWithAnUnfingerprintableField:
    """A field of a type nothing here has a case for: neither natively JSON-serializable nor a
    contract model, so it must raise rather than silently fall back to some string form of it that
    could mask a real difference between two otherwise-distinct values."""

    reference_date: date
    created_at: datetime
    customer_id: str
    unfingerprintable: set[str]


def test_a_field_of_an_unsupported_type_raises_rather_than_guessing() -> None:
    content = _ContentWithAnUnfingerprintableField(
        reference_date=_REFERENCE_DATE,
        created_at=_CREATED_AT,
        customer_id="CLI-1234",
        unfingerprintable={"a", "b"},
    )

    with pytest.raises(TypeError):
        content_fingerprint(content)  # type: ignore[arg-type]
