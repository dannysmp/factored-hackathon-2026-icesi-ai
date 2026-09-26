"""
Dispute Policy Engine
=====================

Overview
--------
Decides, for one dispute request, whether it is eligible to be filed, cannot be filed, or must
be handled by a person, and says why with a stable reason code and the facts the decision used.
It is the single implementation of the rules: the controller acts on it and the explanations
quote it, so what is enforced and what is said cannot disagree.

Scope
-----
In: the eligibility and routing rules, applied to a validated request and a loaded policy.
Out: reading policy files (``loader``), gathering the facts of a request, and filing the case.

Design Principles
-----------------
- Pure function: no I/O, no clock and no randomness. Today's date is a parameter, so the same
  request, policy and date always give the same decision.
- Fixed order. Eligibility gates first, because a person cannot file what the bank cannot
  dispute; then routing to a person for what could be filed; otherwise the request is eligible.
- A gate that fails ends the evaluation with the first failing reason. Routing collects every
  trigger that applies, so the person receiving the case sees all of them.

Business Flow
-------------
1) Product in scope, transaction type disputable, status approved (in this order).
2) Transaction date not in the future, and within the filing window of the category.
3) No open dispute case already exists for the transaction.
4) Routing: fraud claim; low confidence in the understood request; repeat complainer; amount at
   or above the threshold, or unknown; risk score at or above the threshold.
5) Otherwise eligible, with the confirmation the category requires.

Runtime Contract
----------------
``evaluate_dispute(request, policy, *, today) -> PolicyDecision``

Limitations
-----------
The engine trusts the facts it is given; verifying them against records is the service layer's
responsibility.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Callable  # Type of an eligibility gate
from datetime import date  # Today's date, passed in by the caller

# Local modules
from app.domain.policy.models import (  # Vocabulary and value objects
    DisputeCategory,
    DisputeRequest,
    Fact,
    Outcome,
    Policy,
    PolicyDecision,
    ReasonCode,
    TransactionStatus,
)

# Reason for a transaction whose status forbids a dispute.
_STATUS_REASONS: dict[TransactionStatus, ReasonCode] = {
    TransactionStatus.DECLINED: ReasonCode.TRANSACTION_DECLINED,
    TransactionStatus.PENDING: ReasonCode.TRANSACTION_PENDING,
    TransactionStatus.REVERSED: ReasonCode.TRANSACTION_REVERSED,
}


def _fact(name: str, value: object) -> Fact:
    """A fact rendered as text."""
    return Fact(name=name, value=str(value))


def _decision(
    policy: Policy,
    outcome: Outcome,
    reason: ReasonCode,
    facts: tuple[Fact, ...],
    *,
    requires_confirmation: bool = False,
    triggers: tuple[ReasonCode, ...] = (),
) -> PolicyDecision:
    """Assemble a decision stamped with the policy version."""
    return PolicyDecision(
        outcome=outcome,
        reason_code=reason,
        policy_version=policy.version,
        requires_confirmation=requires_confirmation,
        facts=facts,
        triggers=triggers,
    )


def _ineligible(policy: Policy, reason: ReasonCode, *facts: Fact) -> PolicyDecision:
    return _decision(policy, Outcome.INELIGIBLE, reason, facts)


def _routing_triggers(request: DisputeRequest, policy: Policy) -> tuple[ReasonCode, ...]:
    """Every routing rule that applies, in the fixed order of the policy."""
    routing = policy.routing
    checks: tuple[tuple[bool, ReasonCode], ...] = (
        (request.category is DisputeCategory.FRAUD_CLAIM, ReasonCode.ESCALATE_FRAUD_CLAIM),
        (
            request.nlu_confidence < routing.nlu_confidence_floor,
            ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,
        ),
        (
            routing.escalate_repeat_complainer and request.is_repeat_complainer,
            ReasonCode.ESCALATE_REPEAT_COMPLAINER,
        ),
        (
            request.amount_usd is not None and request.amount_usd >= routing.escalate_amount_usd,
            ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD,
        ),
        (
            routing.escalate_unknown_amount and request.amount_usd is None,
            ReasonCode.ESCALATE_AMOUNT_UNKNOWN,
        ),
        (
            request.risk_score is not None and request.risk_score >= routing.risk_score_threshold,
            ReasonCode.ESCALATE_RISK_SCORE,
        ),
    )
    return tuple(reason for applies, reason in checks if applies)


def _routing_facts(request: DisputeRequest, policy: Policy) -> tuple[Fact, ...]:
    """The facts behind the routing rules, for the person who receives the case."""
    routing = policy.routing
    amount = "unknown" if request.amount_usd is None else request.amount_usd
    facts = [
        _fact("category", request.category.value),
        _fact("nlu_confidence", request.nlu_confidence),
        _fact("nlu_confidence_floor", routing.nlu_confidence_floor),
        _fact("is_repeat_complainer", request.is_repeat_complainer),
        _fact("amount_usd", amount),
        _fact("escalate_amount_usd", routing.escalate_amount_usd),
    ]
    if request.risk_score is not None:
        facts += [
            _fact("risk_score", request.risk_score),
            _fact("risk_score_threshold", routing.risk_score_threshold),
        ]
    return tuple(facts)


def _age_days(request: DisputeRequest, today: date) -> int:
    """Days from the transaction date to ``today``; negative for a date in the future."""
    return (today - request.transaction_date).days


def _gate_product(request: DisputeRequest, policy: Policy, today: date) -> PolicyDecision | None:
    if request.product_type in policy.in_scope_product_types:
        return None
    return _ineligible(
        policy, ReasonCode.PRODUCT_OUT_OF_SCOPE, _fact("product_type", request.product_type)
    )


def _gate_type(request: DisputeRequest, policy: Policy, today: date) -> PolicyDecision | None:
    if request.transaction_type in policy.disputable_transaction_types:
        return None
    return _ineligible(
        policy,
        ReasonCode.TRANSACTION_TYPE_NOT_DISPUTABLE,
        _fact("transaction_type", request.transaction_type),
    )


def _gate_status(request: DisputeRequest, policy: Policy, today: date) -> PolicyDecision | None:
    if request.transaction_status is TransactionStatus.APPROVED:
        return None
    return _ineligible(
        policy,
        _STATUS_REASONS[request.transaction_status],
        _fact("transaction_status", request.transaction_status.value),
    )


def _gate_future_date(
    request: DisputeRequest, policy: Policy, today: date
) -> PolicyDecision | None:
    if _age_days(request, today) >= 0:
        return None
    return _ineligible(
        policy,
        ReasonCode.TRANSACTION_DATE_IN_FUTURE,
        _fact("transaction_date", request.transaction_date),
    )


def _gate_window(request: DisputeRequest, policy: Policy, today: date) -> PolicyDecision | None:
    """The last valid day is the day equal to the window of the category."""
    window = policy.categories[request.category].filing_window_days
    age_days = _age_days(request, today)
    if age_days <= window:
        return None
    return _ineligible(
        policy,
        ReasonCode.FILING_WINDOW_EXPIRED,
        _fact("category", request.category.value),
        _fact("age_days", age_days),
        _fact("filing_window_days", window),
    )


def _gate_open_case(request: DisputeRequest, policy: Policy, today: date) -> PolicyDecision | None:
    if not request.has_open_case_for_transaction:
        return None
    return _ineligible(policy, ReasonCode.DUPLICATE_OPEN_CASE)


# Eligibility gates in the order they are applied; the first that fails decides.
_Gate = Callable[[DisputeRequest, Policy, date], PolicyDecision | None]
_GATES: tuple[_Gate, ...] = (
    _gate_product,
    _gate_type,
    _gate_status,
    _gate_future_date,
    _gate_window,
    _gate_open_case,
)


def evaluate_dispute(request: DisputeRequest, policy: Policy, *, today: date) -> PolicyDecision:
    """Decide what the policy says about a dispute request.

    Parameters
    ----------
    request : DisputeRequest
        The facts of the request, already validated.
    policy : Policy
        The policy version in force.
    today : date
        The date the filing window is measured to; never read from the clock here.

    Returns
    -------
    PolicyDecision
        ``eligible`` (with the confirmation required), ``ineligible`` with the first gate that
        failed, or ``escalate`` with every routing trigger that applies.

    Notes
    -----
    Pure and deterministic: the same arguments always give the same decision.
    """
    # Apply the eligibility gates in order; the first failure decides
    for gate in _GATES:
        failed = gate(request, policy, today)
        if failed is not None:
            return failed

    # Route to a person when any routing rule applies
    triggers = _routing_triggers(request, policy)
    if triggers:
        return _decision(
            policy,
            Outcome.ESCALATE,
            triggers[0],
            _routing_facts(request, policy),
            triggers=triggers,
        )

    # Eligible: the customer confirms the exact filing when the category requires it
    rule = policy.categories[request.category]
    return _decision(
        policy,
        Outcome.ELIGIBLE,
        ReasonCode.ELIGIBLE,
        (
            _fact("category", request.category.value),
            _fact("age_days", _age_days(request, today)),
            _fact("filing_window_days", rule.filing_window_days),
        ),
        requires_confirmation=rule.requires_confirmation,
    )
