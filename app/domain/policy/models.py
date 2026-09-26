"""
Dispute Policy Models
=====================

Overview
--------
The typed vocabulary of the dispute policy: the categories a customer can dispute under, the
parameters the policy is made of, the facts a decision needs and the decision itself with its
stable reason code. Every model is immutable and rejects unknown fields, so a misspelled key in
a policy file or a request fails at the boundary instead of silently doing nothing.

Scope
-----
In: value objects and closed sets (enumerations) of the policy.
Out: applying the rules (``engine``) and reading policy files (``loader``).

Design Principles
-----------------
- Closed sets are enumerations, never free strings: a new category, outcome or reason code is a
  deliberate change that every exhaustive check will notice.
- Reason codes are a stable contract: dashboards, audit records and the evaluation refer to them,
  so a code is never renamed, only added.
- The policy parameters are data (versioned files), the rules are code; a parameter change never
  needs a code change and a rule change never hides in a parameter.

Runtime Contract
----------------
``Policy``, ``DisputeRequest`` and ``PolicyDecision`` with the enumerations ``DisputeCategory``,
``TransactionStatus``, ``Outcome`` and ``ReasonCode``.

Limitations
-----------
The parameter values in the shipped policy file are synthetic planning values, not those of any
bank or regulator.
"""

from __future__ import annotations

# Standard libraries
from datetime import date  # Transaction date of a request
from decimal import Decimal  # Money is never a float
from enum import StrEnum  # Closed sets of the policy vocabulary
from typing import Annotated  # Bounded numeric fields

# Third-party libraries
from pydantic import BaseModel, ConfigDict, Field, model_validator  # Validated immutable models

# -----------------------------------------------------------------------------
# Vocabulary
# -----------------------------------------------------------------------------


class DisputeCategory(StrEnum):
    """What the customer disputes about a transaction."""

    UNRECOGNIZED_CHARGE = "unrecognized_charge"
    DUPLICATE_CHARGE = "duplicate_charge"
    WRONG_AMOUNT = "wrong_amount"
    SERVICE_NOT_RECEIVED = "service_not_received"
    FRAUD_CLAIM = "fraud_claim"


class TransactionStatus(StrEnum):
    """Status of the transaction as the source records it."""

    APPROVED = "Approved"
    DECLINED = "Declined"
    PENDING = "Pending"
    REVERSED = "Reversed"


class Outcome(StrEnum):
    """What the policy decides about a dispute request."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    ESCALATE = "escalate"


class ReasonCode(StrEnum):
    """Why the policy decided as it did. Codes are stable: they are added, never renamed."""

    ELIGIBLE = "eligible"
    PRODUCT_OUT_OF_SCOPE = "product_out_of_scope"
    TRANSACTION_TYPE_NOT_DISPUTABLE = "transaction_type_not_disputable"
    TRANSACTION_DECLINED = "transaction_declined"
    TRANSACTION_PENDING = "transaction_pending"
    TRANSACTION_REVERSED = "transaction_reversed"
    TRANSACTION_DATE_IN_FUTURE = "transaction_date_in_future"
    FILING_WINDOW_EXPIRED = "filing_window_expired"
    DUPLICATE_OPEN_CASE = "duplicate_open_case"
    ESCALATE_FRAUD_CLAIM = "escalate_fraud_claim"
    ESCALATE_LOW_NLU_CONFIDENCE = "escalate_low_nlu_confidence"
    ESCALATE_REPEAT_COMPLAINER = "escalate_repeat_complainer"
    ESCALATE_AMOUNT_ABOVE_THRESHOLD = "escalate_amount_above_threshold"
    ESCALATE_AMOUNT_UNKNOWN = "escalate_amount_unknown"
    ESCALATE_RISK_SCORE = "escalate_risk_score"


# -----------------------------------------------------------------------------
# Policy parameters
# -----------------------------------------------------------------------------

Rate = Annotated[float, Field(ge=0, le=1)]


class _Frozen(BaseModel):
    """Base of every model: immutable, and unknown fields are an error."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CategoryRule(_Frozen):
    """Filing rules of one dispute category."""

    filing_window_days: Annotated[int, Field(ge=1, le=3650)]
    requires_confirmation: bool


class RoutingRules(_Frozen):
    """When a request that could be filed is routed to a person instead."""

    escalate_amount_usd: Annotated[Decimal, Field(gt=0)]
    nlu_confidence_floor: Rate
    risk_score_threshold: Rate
    escalate_repeat_complainer: bool
    escalate_unknown_amount: bool


class Policy(_Frozen):
    """One version of the dispute policy."""

    version: Annotated[str, Field(min_length=1)]
    provenance: Annotated[str, Field(min_length=1)]
    in_scope_product_types: frozenset[str]
    disputable_transaction_types: frozenset[str]
    categories: dict[DisputeCategory, CategoryRule]
    routing: RoutingRules

    @model_validator(mode="after")
    def _every_category_has_a_rule(self) -> Policy:
        """A category without a rule would be undecidable; refuse the policy."""
        missing = set(DisputeCategory) - set(self.categories)
        if missing:
            names = ", ".join(sorted(category.value for category in missing))
            raise ValueError(f"policy has no rule for: {names}")
        return self

    @model_validator(mode="after")
    def _scopes_are_not_empty(self) -> Policy:
        """A policy that accepts no product or no transaction type could never file anything."""
        if not self.in_scope_product_types or not self.disputable_transaction_types:
            raise ValueError("in_scope_product_types and disputable_transaction_types are required")
        return self


# -----------------------------------------------------------------------------
# Request and decision
# -----------------------------------------------------------------------------


class DisputeRequest(_Frozen):
    """The facts a decision needs, gathered by the service layer from trusted records."""

    category: DisputeCategory
    transaction_date: date
    transaction_status: TransactionStatus
    transaction_type: str
    product_type: str
    amount_usd: Annotated[Decimal, Field(ge=0)] | None
    nlu_confidence: Rate
    is_repeat_complainer: bool
    has_open_case_for_transaction: bool
    risk_score: Rate | None = None


class Fact(_Frozen):
    """One fact a decision rests on, rendered as text so it can be shown and audited."""

    name: str
    value: str


class PolicyDecision(_Frozen):
    """The outcome of the policy for one request, with everything needed to explain it."""

    outcome: Outcome
    reason_code: ReasonCode
    policy_version: str
    requires_confirmation: bool
    facts: tuple[Fact, ...]
    triggers: tuple[ReasonCode, ...] = ()
