"""
Case Contract Tests
====================

Component: ``contracts.service_v1.cases``. Hermetic and pure: the contracts are declarative
models, so the tests check what they accept, what they refuse, and what they cannot carry.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.domain.policy.models import DisputeCategory, ReasonCode
from contracts.service_v1.cases import (
    AmountProvenance,
    CaseRecord,
    CaseStatus,
    DisclosedAmount,
    Money,
)


def _record(**overrides: object) -> CaseRecord:
    """Build a valid open case record, with ``overrides`` applied on top of the defaults.

    Parameters
    ----------
    **overrides : object
        Field values that replace the defaults, so a test varies exactly one thing.

    Raises
    ------
    pydantic.ValidationError
        The overrides make the record invalid; tests rely on this to probe each refusal.
    """
    fields: dict[str, object] = {
        "case_number": "CASE-1",
        "status": CaseStatus.OPEN,
        "transaction_ref": "TX-1",
        "category": DisputeCategory.UNRECOGNIZED_CHARGE,
        "amount": DisclosedAmount(
            money=Money(amount="10.00", currency="COP"), provenance=AmountProvenance.REPORTED
        ),
        "domain_date": date(2026, 1, 10),
        "expected_first_response_date": date(2026, 1, 20),
        "created_at_utc": datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
        "policy_version": "1",
        "reason_code": ReasonCode.ELIGIBLE,
        "language": "es",
    }
    fields.update(overrides)
    return CaseRecord(**fields)


def test_a_disclosed_amount_carries_no_figure_when_unknown() -> None:
    """The unknown provenance is the only one that carries no money."""
    amount = DisclosedAmount(money=None, provenance=AmountProvenance.UNKNOWN)

    assert amount.money is None


@pytest.mark.parametrize("provenance", [AmountProvenance.REPORTED, AmountProvenance.CONVERTED])
def test_a_reported_or_converted_amount_requires_a_figure(provenance: AmountProvenance) -> None:
    """A known provenance without a figure would show nothing for something ``known``."""
    with pytest.raises(ValidationError, match="money is present exactly"):
        DisclosedAmount(money=None, provenance=provenance)


def test_an_unknown_amount_carries_no_figure() -> None:
    """The reverse direction: a figure present but marked unknown is also refused."""
    with pytest.raises(ValidationError, match="money is present exactly"):
        DisclosedAmount(
            money=Money(amount="10.00", currency="COP"), provenance=AmountProvenance.UNKNOWN
        )


def test_case_status_includes_rejected() -> None:
    """Rejected is part of the closed set, reachable only by an agent write."""
    assert set(CaseStatus) == {
        CaseStatus.OPEN,
        CaseStatus.IN_REVIEW,
        CaseStatus.RESOLVED,
        CaseStatus.REJECTED,
    }
    assert _record(status=CaseStatus.REJECTED).status is CaseStatus.REJECTED


def test_a_case_record_builds_from_valid_fields() -> None:
    """The happy path: every field is present and internally consistent."""
    record = _record()

    assert record.status is CaseStatus.OPEN
    assert record.reason_code is ReasonCode.ELIGIBLE


def test_the_expected_response_is_not_before_the_domain_date() -> None:
    """The first response cannot be expected before the case was filed."""
    with pytest.raises(ValidationError, match="expected_first_response_date is before"):
        _record(domain_date=date(2026, 1, 20), expected_first_response_date=date(2026, 1, 10))


def test_the_expected_response_may_equal_the_domain_date() -> None:
    """The boundary is inclusive: filed and expected to respond on the same day is valid."""
    record = _record(domain_date=date(2026, 1, 10), expected_first_response_date=date(2026, 1, 10))

    assert record.expected_first_response_date == record.domain_date


def test_a_naive_created_at_is_refused() -> None:
    """The real instant of filing must carry a timezone; a naive instant is refused."""
    with pytest.raises(ValidationError):
        _record(created_at_utc=datetime(2026, 1, 10, 7, 0))


def test_a_created_at_with_a_nonzero_offset_is_refused() -> None:
    """An aware instant that is not itself UTC is still refused, not silently converted."""
    bogota = timezone(timedelta(hours=-5))

    with pytest.raises(ValidationError, match="UTC"):
        _record(created_at_utc=datetime(2026, 1, 10, 7, 0, tzinfo=bogota))


def test_a_case_record_rejects_an_unknown_field() -> None:
    """The contract is closed: a misspelled key fails at the boundary."""
    with pytest.raises(ValidationError):
        _record(case_id="unexpected")


def test_a_case_record_is_immutable() -> None:
    """A built record cannot be edited in place."""
    record = _record()

    with pytest.raises(ValidationError):
        record.status = CaseStatus.RESOLVED  # type: ignore[misc]
