"""
Fact Translation
================

Overview
--------
Translates a tool-layer record (``contracts.service_v1.tools``/``cases``, read straight from the
serving store) into the render-layer fact the envelope and the handoff packet carry
(``contracts.service_v1.envelope``). The two contracts are deliberately separate, so the mapping
lives in one place and nothing recomputes it ad hoc at every call site that needs one.

Scope
-----
In: ``to_envelope_transaction``, ``to_envelope_case`` — pure, no I/O, no policy.
Out: resolving a record in the first place (the tool port) and deciding which one an envelope
carries (the dialogue controller).

Design Principles
-----------------
- One direction only: from what the store returned to what a reply may state. Nothing here builds
  a tool-layer request from an envelope.
- An unknown amount stays unknown: ``DisclosedAmount.money`` is already ``None`` exactly when the
  source gives no figure and no same-day conversion exists; this module carries that absence
  through rather than inventing a placeholder, matching ``envelope.TransactionFact.amount``'s own
  optionality.
- The provenance of an amount (reported, converted, unknown) is not customer-facing and stays
  behind at this boundary; the envelope only ever sees the figure or its absence.

Runtime Contract
----------------
``to_envelope_transaction(fact: tools.TransactionFact) -> envelope.TransactionFact``.
``to_envelope_case(record: cases.CaseRecord) -> envelope.CaseFact``.
"""

from __future__ import annotations

# Local modules
from contracts.service_v1 import cases, tools
from contracts.service_v1.envelope import CaseFact, Money, ProductLabel, TransactionFact


def to_envelope_transaction(fact: tools.TransactionFact) -> TransactionFact:
    """``fact`` as the renderer or a handoff packet may state it.

    The amount is the disclosed figure when the source gave one (or a same-day conversion exists)
    and ``None`` otherwise; where the figure came from is not carried over.
    """
    money = fact.amount.money
    return TransactionFact(
        ref=fact.ref,
        occurred_on=fact.occurred_on,
        merchant=fact.merchant,
        amount=Money(amount=money.amount, currency=money.currency) if money is not None else None,
        product=ProductLabel(name=fact.product.name, last4=fact.product.last4),
        status=fact.status,
    )


def to_envelope_case(record: cases.CaseRecord) -> CaseFact:
    """``record`` as the renderer or a handoff packet may state it.

    The case's status is its enum value; the filing date and the expected first-response date are
    the record's domain dates.
    """
    return CaseFact(
        case_number=record.case_number,
        status=record.status.value,
        filed_on=record.domain_date,
        transaction_ref=record.transaction_ref,
        expected_response_on=record.expected_first_response_date,
    )
