"""
Tool Contract and Port, Service Version 1
==========================================

Overview
--------
The customer tools: what each takes, what it returns, and the permission invariant it enforces
itself, beyond the session scoping every tool has. The port (``ToolPort``) is the interface the
dialogue controller codes against; an implementation reads and writes the serving store behind
it.

Scope
-----
In: the tool request and result types, the closed set of tools and the permission invariants each
one enforces (the permissions table), and ``ToolPort`` itself.
Out: implementing a tool, applying policy (``app.domain.policy``, called by the controller, never
by a tool), and everything the controller does with a result.

Design Principles
-----------------
- Every tool is session-scoped: the customer comes from the authenticated session, and no request
  type here has a field for one. Middleware injects the customer identifier into the port call
  itself, not into a request field, so a tool cannot express another customer's data.
- A tool that only reads states plainly when there is no match; that is a normal result, not an
  error. A ``ToolFailure`` is reserved for what the store itself could not do, and it is always
  retryable unless it says otherwise; a partial list is never returned.
- The create tool enforces permission invariants only and never evaluates policy: it refuses only
  what it can verify itself (missing or mismatched confirmation, idempotency-key reuse, the
  per-session cap, a duplicate open case, or a request the controller passed no decision for), and
  it never trusts a caller-stated amount, category or transaction over the decision it was given.
  ``not_eligible`` and ``requires_person`` are not codes of this tool: policy eligibility is the
  controller's decision, made before the tool is ever called.
- The permissions table is exhaustive over the closed set of tools: a tool added to ``Tool``
  without an entry in ``PERMISSIONS`` fails the tests, so no tool ships without its permissions
  declared.
- The models are immutable and reject unknown fields, so a misspelled key fails at the boundary.

Runtime Contract
-----------------
``Tool``, ``Permission``, ``PERMISSIONS``; ``ToolRefusalCode``; ``TransactionFact``,
``TransactionFilters``, ``TransactionPage``; ``EvaluateDisputeRequest``;
``CreateDisputeCaseRequest``, ``CreateDisputeCaseResult``; ``ToolFailure``; the port ``ToolPort``.

Limitations
-----------
This contract does not cover policy retrieval or the handoff: their request and result types are
declared with the retrieval and handoff components that implement them. The reference and pattern
types here are not themselves validated at this boundary; the concrete adapter validates a raw
string ref before it reaches the store.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Mapping  # Type of the permissions table
from datetime import date  # Transaction date and the optional filter bounds
from enum import StrEnum  # Closed sets of the contract
from typing import Annotated, Literal, Protocol  # Bounded fields and the port interface

# Third-party libraries
from pydantic import Field, model_validator  # Field bounds and cross-field rules

# Local modules
from app.domain.policy.models import (
    DisputeCategory,
    PolicyDecision,
    TransactionStatus,
)  # One vocabulary, not redefined
from contracts.service_v1.cases import (  # The base this contract builds on
    NUMBER_PATTERN,
    REF_PATTERN,
    CaseRecord,
    ContractModel,
    DisclosedAmount,
    Money,
    SafeText,
)

# -----------------------------------------------------------------------------
# Tools and the permissions table
# -----------------------------------------------------------------------------


class Tool(StrEnum):
    """The closed set of customer tools this contract declares."""

    LIST_TRANSACTIONS = "list_transactions"
    GET_TRANSACTION = "get_transaction"
    LIST_DISPUTE_CASES = "list_dispute_cases"
    GET_CASE = "get_case"
    EVALUATE_DISPUTE = "evaluate_dispute"
    CREATE_DISPUTE_CASE = "create_dispute_case"


class Permission(StrEnum):
    """One invariant a tool enforces itself, beyond the session scoping every tool has."""

    # The request is marked confirmed, and the decision it carries is for the same transaction
    # and category.
    CONFIRMED = "confirmed"
    # An idempotency key repeated with the same transaction and category replays the original
    # filing; with a different payload it conflicts.
    IDEMPOTENT = "idempotent"
    # A session files at most a bounded number of cases.
    SESSION_CREATE_CAP = "session_create_cap"
    # A transaction with an open case on file cannot be disputed again.
    NO_DUPLICATE_OPEN_CASE = "no_duplicate_open_case"
    # The request carries the controller's own decision; the tool never evaluates policy itself.
    CONTROLLER_ONLY = "controller_only"


# Exhaustive over Tool (tested): a tool added without an entry here fails the tests. Only the
# create tool enforces anything beyond session scoping.
PERMISSIONS: Mapping[Tool, frozenset[Permission]] = {
    Tool.LIST_TRANSACTIONS: frozenset(),
    Tool.GET_TRANSACTION: frozenset(),
    Tool.LIST_DISPUTE_CASES: frozenset(),
    Tool.GET_CASE: frozenset(),
    Tool.EVALUATE_DISPUTE: frozenset(),
    Tool.CREATE_DISPUTE_CASE: frozenset(
        {
            Permission.CONFIRMED,
            Permission.IDEMPOTENT,
            Permission.SESSION_CREATE_CAP,
            Permission.NO_DUPLICATE_OPEN_CASE,
            Permission.CONTROLLER_ONLY,
        }
    ),
}


class ToolRefusalCode(StrEnum):
    """Why the create tool refused a request. Policy eligibility is never one of these."""

    # The request is not marked confirmed.
    CONFIRMATION_REQUIRED = "confirmation_required"
    # The decision carried is for a different transaction or category than the request names.
    CONFIRMATION_MISMATCH = "confirmation_mismatch"
    # The idempotency key was already used with a different payload.
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    # The transaction already has an open case.
    DUPLICATE_OPEN_CASE = "duplicate_open_case"
    # The session has reached its cap of filed cases.
    SESSION_CAP_REACHED = "session_cap_reached"
    # The controller passed no decision, so the tool cannot verify anything and refuses.
    DECISION_MISSING = "decision_missing"


# -----------------------------------------------------------------------------
# Reads
# -----------------------------------------------------------------------------


class ProductLabel(ContractModel):
    """A product by its name and the last four digits of its number, nothing more."""

    name: Annotated[SafeText, Field(min_length=1, max_length=60)]
    last4: Annotated[str, Field(pattern=r"^\d{4}$")]


class TransactionFact(ContractModel):
    """One of the session customer's own transactions.

    The merchant name is absent for most of the source data; a customer render shows it when
    present and falls back to ``description`` (the source's own transaction description)
    otherwise. The tool implementation decides which one a page carries; this contract
    only says that either, both or neither may be present, never inventing one from the other.
    """

    # Opaque reference of the transaction, the handle every later call uses.
    ref: Annotated[str, Field(pattern=REF_PATTERN)]
    occurred_on: date
    # Required key but nullable: the source often has no merchant.
    merchant: Annotated[SafeText, Field(min_length=1, max_length=80)] | None
    description: Annotated[SafeText, Field(min_length=1, max_length=200)] | None = None
    amount: DisclosedAmount
    # The amount in the currency the transaction was made in, as the source states it. ``amount``
    # is always in US dollars, so a figure the customer quotes in another currency can only be
    # matched here. Absent where a caller has no such figure.
    original_amount: Money | None = None
    product: ProductLabel
    status: TransactionStatus


class TransactionFilters(ContractModel):
    """Optional narrowing of the session customer's own transactions; never a customer field."""

    # Bounds on the transaction date; both inclusive, each optional.
    since: date | None = None
    until: date | None = None

    @model_validator(mode="after")
    def _since_is_not_after_until(self) -> TransactionFilters:
        """A window whose start is after its end could never match anything."""
        if self.since is not None and self.until is not None and self.since > self.until:
            raise ValueError("since must not be after until")
        return self


class TransactionPage(ContractModel):
    """At most five of the session customer's own transactions, most recent first."""

    items: Annotated[tuple[TransactionFact, ...], Field(max_length=5)] = ()
    # How many transactions match in all, which may exceed the five listed.
    total_count: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def _total_covers_the_page(self) -> TransactionPage:
        """The count of matches is never below the transactions listed."""
        if self.total_count < len(self.items):
            raise ValueError("total_count is below the number of items listed")
        return self


class ToolFailure(ContractModel):
    """The store could not answer; distinct from a normal, matchless read.

    Always retryable unless stated otherwise, and never a partial result: a page or a record is
    returned whole, or not at all.
    """

    tool: Tool
    # "circuit_open" means the call was not attempted because repeated failures opened the breaker.
    cause: Literal["timeout", "error", "circuit_open"]
    retryable: bool = True


class EvaluateDisputeRequest(ContractModel):
    """What the customer states about their own transaction, evaluated against policy.

    Pure: evaluating has no side effect, and calling it twice with the same request yields the
    same decision. The transaction itself is looked up server-side from ``transaction_ref``; nothing
    about it is trusted from the caller.
    """

    transaction_ref: Annotated[str, Field(pattern=REF_PATTERN)]
    category: DisputeCategory


# -----------------------------------------------------------------------------
# Case creation
# -----------------------------------------------------------------------------


class CreateDisputeCaseRequest(ContractModel):
    """A case creation, refused unless every permission invariant holds.

    The tool does not evaluate policy: ``decision`` is the controller's own record of an eligible
    outcome that requires confirmation, for this same transaction and category. When the
    controller passes no decision, the tool refuses and creates nothing (fail closed).
    """

    transaction_ref: Annotated[str, Field(pattern=REF_PATTERN)]
    category: DisputeCategory
    # Whether the customer explicitly confirmed the filing.
    confirmed: bool
    # Chosen by the caller; repeating it with the same payload replays the original filing.
    idempotency_key: Annotated[str, Field(pattern=NUMBER_PATTERN)]
    decision: PolicyDecision | None = None


class CreateDisputeCaseResult(ContractModel):
    """Either the case was created, or refused for a permission reason — never both.

    A ``duplicate_open_case`` refusal names the case already on file for the transaction in
    ``existing_case_number``, so the customer does not have to look it up separately.
    """

    # Exactly one of ``created`` and ``refusal`` holds; ``case_number`` goes with ``created``.
    created: bool
    case_number: Annotated[str, Field(pattern=NUMBER_PATTERN)] | None = None
    refusal: ToolRefusalCode | None = None
    existing_case_number: Annotated[str, Field(pattern=NUMBER_PATTERN)] | None = None

    @model_validator(mode="after")
    def _created_xor_refused(self) -> CreateDisputeCaseResult:
        """A result is exactly one of a created case and a refusal, never both, never neither."""
        if self.created == (self.refusal is not None):
            raise ValueError("created and refusal are mutually exclusive")
        if self.created != (self.case_number is not None):
            raise ValueError("case_number is present exactly when the case was created")
        if (self.existing_case_number is not None) != (
            self.refusal == ToolRefusalCode.DUPLICATE_OPEN_CASE
        ):
            raise ValueError(
                "existing_case_number is present exactly when refused as duplicate_open_case"
            )
        return self


# -----------------------------------------------------------------------------
# Port
# -----------------------------------------------------------------------------


class ToolPort(Protocol):
    """The customer tools the dialogue controller calls; the session's customer is already bound.

    An implementation receives the authenticated customer identifier once, at construction or
    per call outside this interface, and never as a request field: every method here reaches only
    that customer's own data.
    """

    def list_transactions(self, filters: TransactionFilters) -> TransactionPage | ToolFailure:
        """The session customer's own transactions matching ``filters``, most recent first."""

    def get_transaction(self, ref: str) -> TransactionFact | ToolFailure | None:
        """The session customer's own transaction ``ref``, or ``None`` if there is no match."""

    def list_dispute_cases(self) -> tuple[CaseRecord, ...] | ToolFailure:
        """Every dispute case the session customer has filed."""

    def get_case(self, case_number: str) -> CaseRecord | ToolFailure | None:
        """The session customer's own case ``case_number``, or ``None`` if there is no match."""

    def evaluate_dispute(
        self, request: EvaluateDisputeRequest
    ) -> PolicyDecision | ToolFailure | None:
        """The policy decision for ``request``, computed fresh; no side effect.

        ``None`` matches the shape of ``get_transaction`` and ``get_case``:
        ``request.transaction_ref`` resolving to no row, or to one this session's customer does
        not own, is a normal matchless result (see the Design Principles), never a
        ``ToolFailure`` — reserved for what the store itself could not do.
        """

    def create_dispute_case(
        self, request: CreateDisputeCaseRequest
    ) -> CreateDisputeCaseResult | ToolFailure:
        """File a case, or refuse for a permission reason; never a policy reason.

        A ``ToolFailure`` covers what neither party to the decision controls: the store cannot be
        reached, or the audit record for the filing cannot be written. Either fails the filing
        closed — no case is created uncounted, and the customer is told it could not be
        completed, never that it succeeded.
        """
