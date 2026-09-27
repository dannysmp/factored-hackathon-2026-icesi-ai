"""
Envelope Contract, Service Version 1
====================================

Overview
--------
The contract from the decision layer to the rendering layer. The envelope holds the facts a reply
may use, the decisions those facts rest on and the sources that ground policy statements; the
renderer receives the envelope and nothing else, and every claim in a reply must map back to it.

Scope
-----
In: the envelope and everything it carries (facts, decisions, sources), the renderer's view of it,
the agent-only routing detail, the closed sets of intents, slots and fixed-wording templates, and
the typed result of a tool that cannot answer.
Out: producing envelopes (the dialogue controller), rendering them and verifying the rendered text.

Design Principles
-----------------
- Two views of one decision. ``Envelope`` is what the controller builds, audits and hands to the
  console; ``RenderEnvelope`` is the same content without ``agent_only``. The reason code, the
  numeric routing inputs, the risk evidence and the NLU confidence exist only in ``agent_only``,
  so a renderer, a prompt or a customer reply cannot carry what the type never held. The customer
  side of a decision is a plain reason from a closed set, and every escalation shares one of them.
- Every fact is typed and masked: an amount always has its currency, a date is always absolute,
  a product shows its last four digits only and a merchant may be absent, never invented.
- Closed sets are enumerations. Values are added, never renamed; an addition is compatible, any
  other change is a new contract package.
- The models are immutable and reject unknown fields, so a misspelled key fails at the boundary.

Runtime Contract
----------------
``Envelope`` (``render_view()`` gives the ``RenderEnvelope``), ``SourceRef``, ``ToolUnavailable``
and the enumerations ``Intent``, ``Slot``, ``TemplateId`` and ``CustomerReason``.
``CONTRACT_VERSION`` is the string every envelope carries.

Limitations
-----------
Tuples and enumerations are immutable, but the contract cannot stop a caller from building an
envelope whose facts are wrong: the controller owns that, and the output verifier checks the
rendered text against what the envelope holds.
"""

from __future__ import annotations

# Standard libraries
from datetime import date  # Absolute dates of facts and of the reference date
from decimal import Decimal  # Money is never a float
from enum import StrEnum  # Closed sets of the contract
from typing import Annotated, Final, Literal  # Bounded fields, constants, closed sets

# Third-party libraries
from pydantic import BaseModel, ConfigDict, Field, model_validator  # Validated immutable models

# Local modules
from app.domain.policy.models import (  # One vocabulary for outcomes, reasons and categories
    DisputeCategory,
    Outcome,
    ReasonCode,
    TransactionStatus,
)

# -----------------------------------------------------------------------------
# Vocabulary
# -----------------------------------------------------------------------------

CONTRACT_VERSION: Final = "1"

Lang = Literal["es", "pt", "en"]
LANGUAGES: tuple[Lang, ...] = ("es", "pt", "en")

Rate = Annotated[float, Field(ge=0, le=1)]


class ContractModel(BaseModel):
    """Base of every contract model: immutable, and unknown fields are an error."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Intent(StrEnum):
    """What the reply is for."""

    CLARIFY = "clarify"
    PRESENT_TRANSACTIONS = "present_transactions"
    CONFIRM_FILING = "confirm_filing"
    FILING_RESULT = "filing_result"
    DISPUTE_STATUS = "dispute_status"
    POLICY_ANSWER = "policy_answer"
    ABSTAIN = "abstain"
    HANDOFF = "handoff"
    FAREWELL = "farewell"


class Slot(StrEnum):
    """The element the conversation is waiting for."""

    TRANSACTION = "transaction"
    TRANSACTION_CHOICE = "transaction_choice"
    REASON = "reason"
    CONFIRMATION = "confirmation"


class CustomerReason(StrEnum):
    """The plain reason a customer may be told; never a reason code.

    Every request routed to a person maps to ``NEEDS_REVIEW``, so a customer cannot tell which
    rule sent it there.
    """

    ELIGIBLE = "eligible"
    WINDOW_EXPIRED = "window_expired"
    PENDING = "pending"
    DECLINED = "declined"
    REVERSED = "reversed"
    DUPLICATE_CASE = "duplicate_case"
    NOT_DISPUTABLE = "not_disputable"
    NEEDS_REVIEW = "needs_review"


class TemplateId(StrEnum):
    """Fixed-wording texts. Each has one wording per language in the renderer."""

    GREETING = "greeting"
    CLARIFY_TRANSACTION = "clarify_transaction"
    CLARIFY_REASON = "clarify_reason"
    CLARIFY_CHOICE = "clarify_choice"
    CLARIFY_CONFIRMATION = "clarify_confirmation"
    LANGUAGE_OFFER = "language_offer"
    PRESENT_ONE = "present_one"
    PRESENT_LIST = "present_list"
    PRESENT_NARROW = "present_narrow"
    NOT_FOUND = "not_found"
    CONFIRM_FILING = "confirm_filing"
    FILING_RESULT = "filing_result"
    FILING_UNVERIFIED = "filing_unverified"
    FILING_CANCELLED = "filing_cancelled"
    INELIGIBLE = "ineligible"
    DISPUTE_STATUS = "dispute_status"
    NO_CASE_FOUND = "no_case_found"
    POLICY_ANSWER = "policy_answer"
    ABSTAIN_POLICY = "abstain_policy"
    REFUSE_UNSUPPORTED = "refuse_unsupported"
    REFUSE_REVERSAL = "refuse_reversal"
    HANDOFF_REVIEW = "handoff_review"
    HANDOFF_FRAUD = "handoff_fraud"
    HANDOFF_CARD_LOSS = "handoff_card_loss"
    HANDOFF_REQUESTED = "handoff_requested"
    HANDOFF_NOT_REGISTERED = "handoff_not_registered"
    RESTART_AFTER_PENDING = "restart_after_pending"
    FAREWELL = "farewell"


# -----------------------------------------------------------------------------
# Facts
# -----------------------------------------------------------------------------


class Money(ContractModel):
    """An amount with its currency code; an amount never appears without one."""

    amount: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class ProductLabel(ContractModel):
    """A product by its name and the last four digits of its number, nothing more."""

    name: Annotated[str, Field(min_length=1, max_length=60)]
    last4: Annotated[str, Field(pattern=r"^\d{4}$")]


class TransactionFact(ContractModel):
    """One of the customer's transactions as the reply may describe it."""

    ref: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    occurred_on: date
    merchant: Annotated[str, Field(min_length=1, max_length=80)] | None
    amount: Money
    product: ProductLabel
    status: TransactionStatus


class CaseFact(ContractModel):
    """One dispute case as the case service holds it."""

    case_number: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")]
    status: Annotated[str, Field(min_length=1, max_length=32)]
    filed_on: date
    transaction_ref: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    expected_response_on: date | None = None


class WindowFact(ContractModel):
    """A filing window stated in days, with the deadline as a date."""

    days_allowed: Annotated[int, Field(ge=1)]
    age_days: Annotated[int, Field(ge=0)]
    deadline: date


class PolicyValue(ContractModel):
    """One parameter of the policy in force, written as the policy states it."""

    name: Annotated[str, Field(min_length=1, max_length=64)]
    value: Annotated[str, Field(min_length=1, max_length=64)]


class DisputeFacts(ContractModel):
    """Everything about the customer's situation a reply may state, masked and session-scoped."""

    transactions: Annotated[tuple[TransactionFact, ...], Field(max_length=5)] = ()
    candidate_count: Annotated[int, Field(ge=0)] = 0
    selected_ref: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")] | None = None
    category: DisputeCategory | None = None
    cases: Annotated[tuple[CaseFact, ...], Field(max_length=3)] = ()
    window: WindowFact | None = None
    policy_values: Annotated[tuple[PolicyValue, ...], Field(max_length=16)] = ()
    expected_response_on: date | None = None
    search_terms: Annotated[
        tuple[Annotated[str, Field(max_length=80)], ...], Field(max_length=6)
    ] = ()
    contact_within_hours: Annotated[int, Field(ge=1)] | None = None
    ticket_ref: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")] | None = None

    @model_validator(mode="after")
    def _candidates_cover_the_transactions(self) -> DisputeFacts:
        """The count of matches is never below the transactions listed."""
        if self.candidate_count < len(self.transactions):
            raise ValueError("candidate_count is below the number of transactions listed")
        return self


# -----------------------------------------------------------------------------
# Decisions and sources
# -----------------------------------------------------------------------------


class Decision(ContractModel):
    """The renderer's view of one policy decision: what was decided and the plain reason."""

    outcome: Outcome
    customer_reason: CustomerReason
    policy_version: Annotated[str, Field(min_length=1)]
    requires_confirmation: bool = False


class InputFact(ContractModel):
    """One input of a decision, written as text for the audit and the console."""

    name: Annotated[str, Field(min_length=1, max_length=64)]
    value: Annotated[str, Field(min_length=1, max_length=64)]


class AgentDecision(ContractModel):
    """The detail of one decision that only an agent may see."""

    reason_code: ReasonCode
    triggers: tuple[ReasonCode, ...] = ()
    inputs: tuple[InputFact, ...] = ()


class RiskEvidence(ContractModel):
    """A risk score with its uncertainty and the base rate it is read against."""

    score: Rate
    interval_low: Rate
    interval_high: Rate
    base_rate: Rate

    @model_validator(mode="after")
    def _interval_brackets_the_score(self) -> RiskEvidence:
        """A score lies within its own interval."""
        if not self.interval_low <= self.score <= self.interval_high:
            raise ValueError("score must lie between interval_low and interval_high")
        return self


class AgentOnly(ContractModel):
    """Routing detail for the audit, the packet and the console; never for a reply."""

    decisions: tuple[AgentDecision, ...] = ()
    nlu_confidence: Rate | None = None
    risk: RiskEvidence | None = None


class LocalizedTitle(ContractModel):
    """The readable title of a policy section in one language."""

    lang: Lang
    text: Annotated[str, Field(min_length=1, max_length=120)]


class SourceRef(ContractModel):
    """A policy section that grounds a statement.

    A reply cites the title in its language; the section identifier stays in the decision record.
    """

    section_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
    titles: tuple[LocalizedTitle, ...]
    corpus_version: Annotated[str, Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def _one_title_per_language(self) -> SourceRef:
        """Every language has exactly one title, so a citation exists in each reply language."""
        if sorted(title.lang for title in self.titles) != sorted(LANGUAGES):
            raise ValueError("titles must hold exactly one title for each of es, pt and en")
        return self

    def title_for(self, lang: Lang) -> str:
        """The readable title in ``lang``."""
        return next(title.text for title in self.titles if title.lang == lang)


# -----------------------------------------------------------------------------
# Envelope
# -----------------------------------------------------------------------------

_ENDING_INTENTS = frozenset({Intent.FAREWELL, Intent.HANDOFF})


class RenderEnvelope(ContractModel):
    """What the renderer receives: the envelope without the agent-only detail."""

    contract_version: Literal["1"] = CONTRACT_VERSION
    session_id: Annotated[str, Field(min_length=1, max_length=64)]
    lang: Lang
    domain_date: date
    intent: Intent
    next_expected: Slot | None = None
    end_session: bool = False
    facts: DisputeFacts = DisputeFacts()
    decisions: tuple[Decision, ...] = ()
    sources: tuple[SourceRef, ...] = ()
    render_mode: Literal["template", "model"] = "template"
    template_id: TemplateId | None = None

    @model_validator(mode="after")
    def _wording_matches_the_mode(self) -> RenderEnvelope:
        """Template mode names its text; model mode never does."""
        if self.render_mode == "template" and self.template_id is None:
            raise ValueError("template mode requires a template_id")
        if self.render_mode == "model" and self.template_id is not None:
            raise ValueError("model mode must not carry a template_id")
        return self

    @model_validator(mode="after")
    def _intent_has_what_it_states(self) -> RenderEnvelope:
        """An intent that states something carries the material for it, and no more than it."""
        facts = self.facts
        if self.intent is Intent.PRESENT_TRANSACTIONS and not facts.transactions:
            raise ValueError("present_transactions requires at least one transaction")
        if self.intent is Intent.CONFIRM_FILING:
            eligible = any(
                d.outcome is Outcome.ELIGIBLE and d.requires_confirmation for d in self.decisions
            )
            if not (eligible and facts.selected_ref and facts.category):
                raise ValueError(
                    "confirm_filing requires an eligible decision that needs confirmation, "
                    "a selected transaction and a category"
                )
        if self.intent is Intent.FILING_RESULT and not facts.cases:
            raise ValueError("filing_result requires the case read back")
        if self.intent is Intent.POLICY_ANSWER and not self.sources:
            raise ValueError("policy_answer requires at least one source")
        if self.intent is Intent.ABSTAIN and (self.sources or facts.policy_values):
            raise ValueError("abstain carries no source and no policy value")
        if self.end_session and self.intent not in _ENDING_INTENTS:
            raise ValueError("only farewell and handoff end a session")
        return self


class Envelope(RenderEnvelope):
    """The full envelope: the renderer's content plus the agent-only detail."""

    agent_only: AgentOnly | None = None

    def render_view(self) -> RenderEnvelope:
        """The same envelope without ``agent_only``, the only form a renderer receives."""
        return RenderEnvelope(**{name: getattr(self, name) for name in RenderEnvelope.model_fields})


# -----------------------------------------------------------------------------
# Tool failure
# -----------------------------------------------------------------------------


class ToolUnavailable(ContractModel):
    """The typed result of a tool that cannot answer; the controller turns it into a handoff."""

    tool: Annotated[str, Field(min_length=1, max_length=64)]
    cause: Literal["timeout", "error", "circuit_open"]
    retryable: bool = False
