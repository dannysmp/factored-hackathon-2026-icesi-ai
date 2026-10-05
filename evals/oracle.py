"""
Independent Case-Row Oracle
============================

Overview
--------
Recomputes the dispute-policy engine's eligibility gates against every stored case row, from the
transaction the case cites and the reference date, and flags a row the engine would not have made
eligible. The guarantee it checks: recomputed from the trusted transaction and the reference date,
no case exists that the engine would not have made eligible, except through the controller.

Scope
-----
In: recomputing the five eligibility gates that depend only on the transaction's own trusted
facts and the case's category and domain date (product scope, transaction type, transaction
status, transaction date not in the future, filing window); comparing the recomputed outcome
against what a filed case implies (``Outcome.ELIGIBLE``).
Out: recomputing routing or escalation. NLU confidence, repeat-complainer and risk-score
thresholds are session-time signals no stored record carries, and the amount threshold's input,
``CaseRecord.amount``, is stored but deliberately not recomputed. The residual risk this oracle
covers is an ineligible case slipping past the controller, not a differing escalation choice.
Also out: the duplicate-open-case gate (a store-level uniqueness invariant enforced at write
time) and reading a live case service (this is a pure function over caller-supplied facts).

Design Principles
------------------
- Pure function: no I/O, no clock, no randomness, mirroring ``app.domain.policy.engine``'s own
  design. The caller supplies the transaction facts, the cases, the policy and the reference date.
- Neutral stand-ins for every field the five checked gates do not read: ``nlu_confidence=1.0``
  (always clears the floor), ``is_repeat_complainer=False``, ``risk_score=None``,
  ``has_open_case_for_transaction=False``, ``amount_usd=Decimal("0")`` (never ``None``, so it
  cannot trigger the "unknown amount" escalation, and below any policy's ``escalate_amount_usd``,
  which must be strictly positive). None of the five gates uses any of them, and none of them can
  trigger a routing escalation either: a recomputed ``Outcome.ESCALATE`` would not be the
  violation this oracle exists to catch, so the oracle's own neutral choices must never produce
  one (`tests/test_oracle.py` pins both assumptions). An engine change that made one of these
  fields matter to eligibility would require this module to change too.
- A fraud claim is a documented exception in the engine itself: it can never resolve to
  ``Outcome.ELIGIBLE`` through the gates (it always escalates at the routing step), so a stored
  fraud-claim case cannot have been filed through the automated eligibility path. The oracle
  marks it **not applicable** rather than raise a false violation.
- A case citing a transaction reference with no known facts is reported as a finding of its own,
  never silently skipped, and treated conservatively as a violation: a case that cites a
  transaction the caller cannot resolve is itself evidence worth reporting.

Runtime Contract
----------------
``TransactionFacts`` (the trusted, re-derivable fields one case's check needs)
``OracleFinding``
``check_case(case, transaction, policy, *, today) -> OracleFinding``
``run_oracle(cases, transactions_by_ref, policy, *, today) -> tuple[OracleFinding, ...]``

Limitations
-----------
A stored fraud-claim case is marked not applicable rather than flagged, although its existence as
a filed case is itself the defect class this oracle targets (the engine can never resolve one to
``Outcome.ELIGIBLE``). The amount-threshold escalation is not recomputed from ``CaseRecord.amount``
although that value is stored. An escalated case never produces a case row, so there is nothing
to check for one. The caller's ``transactions_by_ref`` mapping is trusted; verifying its
provenance against ``data/gold/ops_seed`` or ``data/gold/eval_bank`` is the caller's
responsibility.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Mapping, Sequence  # Types of the oracle's inputs
from dataclasses import dataclass  # Immutable inputs and result objects
from datetime import date  # The reference date the recomputation uses
from decimal import Decimal  # A routing-neutral amount

# Local modules
from app.domain.policy import (  # The single implementation the oracle reuses, never redefines
    DisputeCategory,
    DisputeRequest,
    Outcome,
    Policy,
    ReasonCode,
    TransactionStatus,
    evaluate_dispute,
)
from contracts.service_v1.cases import CaseRecord  # The stored case row this oracle audits

# Every field the five checked gates never read, chosen so no routing trigger fires either (a
# trigger would recompute Outcome.ESCALATE, which is not a violation this oracle claims to
# detect, but would corrupt the outcome this module actually checks). `amount_usd=Decimal("0")`
# is never None (no "unknown amount" trigger) and is below every policy's `escalate_amount_usd`,
# which `RoutingRules` requires to be strictly positive.
_NEUTRAL_REQUEST_FIELDS: dict[str, object] = {
    "amount_usd": Decimal("0"),
    "nlu_confidence": 1.0,
    "is_repeat_complainer": False,
    "has_open_case_for_transaction": False,
    "risk_score": None,
}


@dataclass(frozen=True, slots=True)
class TransactionFacts:
    """The trusted, re-derivable facts of one transaction: everything the oracle's eligibility
    recomputation needs, and nothing an after-the-fact check should trust beyond it."""

    transaction_date: date
    transaction_status: TransactionStatus
    transaction_type: str
    product_type: str


@dataclass(frozen=True, slots=True)
class OracleFinding:
    """The oracle's verdict for one stored case row.

    ``applicable`` is ``False`` for a fraud claim and for a case whose transaction is unknown;
    ``violation`` is ``True`` when the case should not exist as filed (a recomputed ineligible
    outcome) or its transaction could not be resolved. ``recomputed_outcome`` and
    ``recomputed_reason_code`` are ``None`` when nothing was recomputed.
    """

    case_number: str
    transaction_ref: str
    applicable: bool
    missing_transaction: bool
    recomputed_outcome: Outcome | None
    recomputed_reason_code: ReasonCode | None
    recorded_reason_code: ReasonCode
    violation: bool


def check_case(
    case: CaseRecord, transaction: TransactionFacts, policy: Policy, *, today: date
) -> OracleFinding:
    """Recompute ``case``'s eligibility from ``transaction``'s trusted facts, as of ``today``.

    Returns
    -------
    OracleFinding
        ``applicable=False`` for a fraud claim (the engine never returns one eligible; see
        ``app.domain.policy.engine``'s own documented exception). Otherwise ``violation=True``
        when the recomputed outcome is ``Outcome.INELIGIBLE`` — the engine would not have filed
        this case on ``today`` from the facts it cites.
    """
    if case.category is DisputeCategory.FRAUD_CLAIM:
        return OracleFinding(
            case_number=case.case_number,
            transaction_ref=case.transaction_ref,
            applicable=False,
            missing_transaction=False,
            recomputed_outcome=None,
            recomputed_reason_code=None,
            recorded_reason_code=case.reason_code,
            violation=False,
        )
    request = DisputeRequest(
        transaction_ref=case.transaction_ref,
        category=case.category,
        transaction_date=transaction.transaction_date,
        transaction_status=transaction.transaction_status,
        transaction_type=transaction.transaction_type,
        product_type=transaction.product_type,
        **_NEUTRAL_REQUEST_FIELDS,
    )
    decision = evaluate_dispute(request, policy, today=today)
    return OracleFinding(
        case_number=case.case_number,
        transaction_ref=case.transaction_ref,
        applicable=True,
        missing_transaction=False,
        recomputed_outcome=decision.outcome,
        recomputed_reason_code=decision.reason_code,
        recorded_reason_code=case.reason_code,
        violation=decision.outcome is Outcome.INELIGIBLE,
    )


def run_oracle(
    cases: Sequence[CaseRecord],
    transactions_by_ref: Mapping[str, TransactionFacts],
    policy: Policy,
    *,
    today: date,
) -> tuple[OracleFinding, ...]:
    """Check every case in ``cases``, in order.

    A case whose ``transaction_ref`` is not a key of ``transactions_by_ref`` is reported as its
    own finding (``missing_transaction=True``, ``violation=True``), never silently skipped.
    """
    findings: list[OracleFinding] = []
    for case in cases:
        transaction = transactions_by_ref.get(case.transaction_ref)
        if transaction is None:
            findings.append(
                OracleFinding(
                    case_number=case.case_number,
                    transaction_ref=case.transaction_ref,
                    applicable=False,
                    missing_transaction=True,
                    recomputed_outcome=None,
                    recomputed_reason_code=None,
                    recorded_reason_code=case.reason_code,
                    violation=True,
                )
            )
            continue
        findings.append(check_case(case, transaction, policy, today=today))
    return tuple(findings)
