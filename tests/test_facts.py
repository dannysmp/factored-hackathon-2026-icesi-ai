"""
Fact Translation Tests
========================

Component: ``app.conversation.facts``. Hermetic and pure: no store, no clock, no network call.
"""

from __future__ import annotations

# Standard libraries
from datetime import date  # Fixed transaction and case dates
from decimal import Decimal  # Money in the tests

# Local modules
from app.conversation.facts import to_envelope_case, to_envelope_transaction
from app.domain.policy.models import DisputeCategory, ReasonCode, TransactionStatus
from contracts.service_v1 import cases, tools
from contracts.service_v1.envelope import CaseFact, TransactionFact

_DOMAIN_DATE = date(2026, 6, 18)


def _tool_transaction(**changes: object) -> tools.TransactionFact:
    values: dict[str, object] = {
        "ref": "tx-1001",
        "occurred_on": date(2026, 6, 12),
        "merchant": "Tienda Sol",
        "amount": cases.DisclosedAmount(
            money=cases.Money(amount=Decimal("250.00"), currency="MXN"),
            provenance=cases.AmountProvenance.REPORTED,
        ),
        "product": tools.ProductLabel(name="Visa Classic", last4="4321"),
        "status": TransactionStatus.APPROVED,
    }
    return tools.TransactionFact(**{**values, **changes})


def _case_record(**changes: object) -> cases.CaseRecord:
    values: dict[str, object] = {
        "case_number": "D-1",
        "status": cases.CaseStatus.OPEN,
        "transaction_ref": "tx-1001",
        "category": DisputeCategory.UNRECOGNIZED_CHARGE,
        "amount": cases.DisclosedAmount(
            money=cases.Money(amount=Decimal("250.00"), currency="MXN"),
            provenance=cases.AmountProvenance.REPORTED,
        ),
        "domain_date": _DOMAIN_DATE,
        "expected_first_response_date": date(2026, 6, 25),
        "created_at_utc": date(2026, 6, 18).isoformat() + "T00:00:00+00:00",
        "policy_version": "2",
        "reason_code": ReasonCode.ELIGIBLE,
        "language": "es",
    }
    return cases.CaseRecord(**{**values, **changes})


def test_a_transaction_with_a_known_amount_carries_it_through() -> None:
    """The figure, currency, merchant and product all translate unchanged."""
    fact = _tool_transaction()

    translated = to_envelope_transaction(fact)

    assert isinstance(translated, TransactionFact)
    assert translated.ref == "tx-1001"
    assert translated.merchant == "Tienda Sol"
    assert translated.amount is not None
    assert translated.amount.amount == Decimal("250.00")
    assert translated.amount.currency == "MXN"
    assert translated.product.last4 == "4321"
    assert translated.status is TransactionStatus.APPROVED


def test_an_unknown_amount_stays_unknown_not_a_placeholder() -> None:
    """A source that gives no figure and no conversion translates to no figure, not a zero."""
    fact = _tool_transaction(
        amount=cases.DisclosedAmount(money=None, provenance=cases.AmountProvenance.UNKNOWN)
    )

    translated = to_envelope_transaction(fact)

    assert translated.amount is None


def test_a_transaction_with_no_merchant_stays_without_one() -> None:
    """An absent merchant is never invented in translation either."""
    fact = _tool_transaction(merchant=None)

    translated = to_envelope_transaction(fact)

    assert translated.merchant is None


def test_a_case_record_translates_its_status_and_dates() -> None:
    """The case's status, dates and references carry through as the renderer expects them."""
    record = _case_record()

    translated = to_envelope_case(record)

    assert isinstance(translated, CaseFact)
    assert translated.case_number == "D-1"
    assert translated.status == "Open"
    assert translated.filed_on == _DOMAIN_DATE
    assert translated.transaction_ref == "tx-1001"
    assert translated.expected_response_on == date(2026, 6, 25)
