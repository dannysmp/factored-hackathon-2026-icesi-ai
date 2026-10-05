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
bank or regulator. Product and transaction types are compared exactly as spelled: the service
layer supplies the canonical spellings of the cleaned data, and any other spelling is out of
scope by design.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping  # Read-only mapping of the category rules

# Standard libraries
from datetime import date  # Transaction date of a request
from decimal import Decimal  # Money is never a float
from enum import StrEnum  # Closed sets of the policy vocabulary
from typing import Annotated, TypeVar  # Bounded numeric fields and the mapping's type parameters

# Third-party libraries
from pydantic import (  # Validated immutable models
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_serializer,
    field_validator,
    model_validator,
)

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

# A rate between 0 and 1 inclusive. For the routing rules the two ends of a comparison differ on
# purpose: a confidence strictly below its floor escalates, a score or an amount at or above its
# threshold escalates.
Rate = Annotated[float, Field(ge=0, le=1)]


class _Frozen(BaseModel):
    """Base of every model: immutable, and unknown fields are an error.

    Rejecting unknown fields makes a misspelled key in a policy file or request fail at the
    boundary instead of being ignored.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


# Key and value types of ReadOnlyMap.
K = TypeVar("K")
V = TypeVar("V")


class ReadOnlyMap(Mapping[K, V]):
    """A mapping that cannot be changed after it is built, yet can be copied and pickled.

    ``types.MappingProxyType`` is read-only but cannot be deep-copied or pickled, which would break
    copying or serialising a policy; this small class keeps the data in a private dictionary and
    exposes reads only.
    """

    def __init__(self, data: Mapping[K, V]) -> None:
        """Keep a private copy of ``data``; later changes to the original do not show through."""
        self._data = dict(data)

    def __getitem__(self, key: K) -> V:
        """The value stored for ``key``; ``KeyError`` when the key is absent."""
        return self._data[key]

    def __iter__(self) -> Iterator[K]:
        """Iterate over the keys in insertion order."""
        return iter(self._data)

    def __len__(self) -> int:
        """The number of entries."""
        return len(self._data)

    def __repr__(self) -> str:
        """The wrapped dictionary, labelled as a ``ReadOnlyMap``."""
        return f"ReadOnlyMap({self._data!r})"


class CategoryRule(_Frozen):
    """Filing rules of one dispute category.

    ``filing_window_days`` is the number of calendar days after the transaction date during which
    a dispute can still be filed (the last valid day is the day equal to the window).
    ``requires_confirmation`` says whether the customer must confirm the exact filing before the
    case is created.
    """

    filing_window_days: Annotated[int, Field(ge=1, le=3650)]
    requires_confirmation: StrictBool


class RoutingRules(_Frozen):
    """When a request that could be filed is routed to a person instead.

    ``escalate_amount_usd`` is the amount, in USD, at or above which a person handles the case;
    ``nlu_confidence_floor`` is the understanding confidence below which a person does;
    ``risk_score_threshold`` is the risk score at or above which a person does, when
    ``risk_routing_enabled`` is on. ``escalate_repeat_complainer`` and ``escalate_unknown_amount``
    switch the corresponding rules on or off. ``clarification_budget`` is the number of consecutive
    clarification attempts on one missing element before the request goes to a person.
    """

    escalate_amount_usd: Annotated[Decimal, Field(gt=0)]
    nlu_confidence_floor: Rate
    risk_score_threshold: Rate
    escalate_repeat_complainer: StrictBool
    escalate_unknown_amount: StrictBool
    # Off while no risk model has cleared its precision floor; fraud claims escalate by
    # category regardless of this flag.
    risk_routing_enabled: StrictBool
    # Consecutive clarification attempts on the same missing element before the request
    # escalates with `escalate_low_nlu_confidence`; the conversation state carries the count.
    clarification_budget: Annotated[int, Field(ge=1, le=10)]

    @field_validator("escalate_amount_usd", mode="before")
    @classmethod
    def _amount_is_not_a_float(cls, value: object) -> object:
        """Refuse a float amount before coercion, because it would carry binary rounding error.

        Money is written as text in the policy file (for example ``"5000.00"``); any other type is
        passed through unchanged for the normal validation to judge.
        """
        if isinstance(value, float):
            raise ValueError('write the amount as text, for example "5000.00"')
        return value


# A stable evidence identifier: the corpus, a reply and a handoff packet all name the same item
# by this key, so it is lower case with no spaces, never free text.
EvidenceId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]


class Policy(_Frozen):
    """One version of the dispute policy: scope, per-category rules, routing and response times.

    Immutable once built. The mapping fields are exposed as read-only maps and the model
    validators refuse a policy that leaves any dispute category without a rule, a first-response
    count or evidence, or that has an empty product or transaction-type scope.
    """

    version: Annotated[str, Field(min_length=1)]
    provenance: Annotated[str, Field(min_length=1)]
    in_scope_product_types: frozenset[str]
    disputable_transaction_types: frozenset[str]
    categories: dict[DisputeCategory, CategoryRule]
    routing: RoutingRules
    # Calendar days from the filing date within which the bank gives its first response.
    first_response_days: dict[DisputeCategory, Annotated[int, Field(ge=1, le=365)]]
    # The evidence identifiers a customer is told to have ready, per category.
    evidence_required: dict[DisputeCategory, tuple[EvidenceId, ...]]

    @field_validator("categories", mode="after")
    @classmethod
    def _categories_are_read_only(
        cls, value: dict[DisputeCategory, CategoryRule]
    ) -> dict[DisputeCategory, CategoryRule]:
        """Expose the rules through a read-only mapping so a loaded policy cannot be edited."""
        return ReadOnlyMap(value)  # type: ignore[return-value]

    @field_validator("first_response_days", mode="after")
    @classmethod
    def _first_response_days_are_read_only(
        cls, value: dict[DisputeCategory, int]
    ) -> dict[DisputeCategory, int]:
        """Expose the counts through a read-only mapping so a loaded policy cannot be edited."""
        return ReadOnlyMap(value)  # type: ignore[return-value]

    @field_validator("evidence_required", mode="after")
    @classmethod
    def _evidence_required_is_read_only(
        cls, value: dict[DisputeCategory, tuple[str, ...]]
    ) -> dict[DisputeCategory, tuple[str, ...]]:
        """Expose the lists through a read-only mapping so a loaded policy cannot be edited."""
        return ReadOnlyMap(value)  # type: ignore[return-value]

    @field_serializer("categories")
    def _serialize_categories(
        self, value: Mapping[DisputeCategory, CategoryRule]
    ) -> dict[DisputeCategory, CategoryRule]:
        """Write the read-only mapping as a plain dictionary."""
        return dict(value)

    @field_serializer("first_response_days")
    def _serialize_first_response_days(
        self, value: Mapping[DisputeCategory, int]
    ) -> dict[DisputeCategory, int]:
        """Write the read-only mapping as a plain dictionary."""
        return dict(value)

    @field_serializer("evidence_required")
    def _serialize_evidence_required(
        self, value: Mapping[DisputeCategory, tuple[str, ...]]
    ) -> dict[DisputeCategory, tuple[str, ...]]:
        """Write the read-only mapping as a plain dictionary."""
        return dict(value)

    @model_validator(mode="after")
    def _every_category_has_a_rule(self) -> Policy:
        """A category without a rule would be undecidable; refuse the policy."""
        missing = set(DisputeCategory) - set(self.categories)
        if missing:
            names = ", ".join(sorted(category.value for category in missing))
            raise ValueError(f"policy has no rule for: {names}")
        return self

    @model_validator(mode="after")
    def _every_category_has_a_response_time(self) -> Policy:
        """A category without a first-response count could never tell the customer one."""
        missing = set(DisputeCategory) - set(self.first_response_days)
        if missing:
            names = ", ".join(sorted(category.value for category in missing))
            raise ValueError(f"policy has no first_response_days for: {names}")
        return self

    @model_validator(mode="after")
    def _every_category_has_evidence(self) -> Policy:
        """A category with no evidence listed would leave the customer nothing to prepare."""
        missing = {c for c in DisputeCategory if not self.evidence_required.get(c)}
        if missing:
            names = ", ".join(sorted(category.value for category in missing))
            raise ValueError(f"policy has no evidence_required for: {names}")
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
    """The facts a decision needs, gathered by the service layer from trusted records.

    ``amount_usd`` is ``None`` when the amount is unknown; ``risk_score`` is ``None`` when no
    score was computed. ``nlu_confidence`` is the confidence in the understood request, from 0 to
    1. ``transaction_type`` and ``product_type`` are compared exactly as spelled.
    """

    transaction_ref: Annotated[str, Field(min_length=1, max_length=64)]
    category: DisputeCategory
    transaction_date: date
    transaction_status: TransactionStatus
    transaction_type: str
    product_type: str
    amount_usd: Annotated[Decimal, Field(ge=0)] | None
    nlu_confidence: Rate
    is_repeat_complainer: StrictBool
    has_open_case_for_transaction: StrictBool
    risk_score: Rate | None = None


class Fact(_Frozen):
    """One fact a decision rests on, rendered as text so it can be shown and audited."""

    name: str
    value: str


class PolicyDecision(_Frozen):
    """The outcome of the policy for one request, with everything needed to explain it.

    ``transaction_ref`` and ``category`` identify the request the decision was made for. They
    carry no rule of their own; they exist so the case-creation tool can refuse
    ``confirmation_mismatch`` by comparing them against a later filing call, without
    trusting the caller and without re-running the policy itself.

    ``reason_code`` is the stable code of the decision; ``triggers`` lists every routing reason
    when the outcome is ``escalate`` (empty otherwise); ``requires_confirmation`` is meaningful
    for an eligible outcome.
    """

    outcome: Outcome
    reason_code: ReasonCode
    policy_version: str
    requires_confirmation: bool
    facts: tuple[Fact, ...]
    triggers: tuple[ReasonCode, ...] = ()
    transaction_ref: Annotated[str, Field(min_length=1, max_length=64)]
    category: DisputeCategory
