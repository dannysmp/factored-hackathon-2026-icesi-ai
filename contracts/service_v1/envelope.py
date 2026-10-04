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
- Two views of one decision, as siblings. ``Envelope`` is what the controller builds, audits and
  hands to the console; ``RenderEnvelope`` is the same content without ``agent_only``. Neither is
  a subclass of the other, so a full envelope is never accepted where the renderer's view is
  required, under the type checker or at runtime. The reason code, the
  numeric routing inputs, the risk evidence and the NLU confidence exist only in ``agent_only``,
  so a renderer, a prompt or a customer reply cannot carry what the type never held. The customer
  side of a decision is a plain reason from a closed set, and every escalation shares one of them.
- Every fact is typed and masked: an amount, when present, always has its currency, a date is
  always absolute, a product shows its last four digits only, and a merchant or an amount may be
  absent, never invented.
- Closed sets are enumerations. Values are added, never renamed; an addition is compatible, any
  other change is a new contract package.
- An envelope that contradicts itself does not build: an outcome and its plain reason agree, a
  fixed text belongs to the intent that carries it, and the agent detail pairs with the decisions.
- Free text is bounded and refuses control characters. Detecting and redacting a card number in
  free text is a masking concern, tested and measured at the egress boundary (the masking
  serializer), not a shape a contract field can enforce without either missing real numbers or
  flagging ordinary ones.
- The models are immutable and reject unknown fields, so a misspelled key fails at the boundary.
- A model-rendered reply never composes a grounded value itself: it names one of a closed set of
  fields for its intent (``GroundedField``, ``INTENT_ALLOWED_FIELDS``), and the output verifier
  substitutes the actual value and rejects any reply that writes a digit on its own instead of
  naming a field. Grounding is a property the verifier enforces structurally, not a pattern it
  detects in finished text — a refusal is exempted by construction, since it always renders from
  the fixed-wording template path.

Runtime Contract
----------------
``Envelope`` (``render_view()`` gives the ``RenderEnvelope``), ``SourceRef``, ``ToolUnavailable``,
the enumerations ``Intent``, ``Slot``, ``TemplateId`` and ``CustomerReason``, the table
``TEMPLATE_INTENTS`` and the mapping ``CUSTOMER_REASON_OF`` from a reason code to its plain reason.
``CONTRACT_VERSION`` is the string every envelope carries. ``GroundedField`` and the tables
``INTENT_ALLOWED_FIELDS``/``INTENT_REQUIRED_FIELDS`` are the vocabulary a model-rendered reply may
cite by name instead of composing itself; ``contracts.service_v1.verification`` is the output
verifier's own request and result types built on that vocabulary.

Limitations
-----------
Tuples and enumerations are immutable, but the contract cannot stop a caller from building an
envelope whose facts are wrong: the controller owns that, and the output verifier checks the
rendered text against what the envelope holds.
``GroundedField`` closes the gap for every digit a reply can state, plus the other facts an intent
names (a merchant, a case status, a policy value); a model-rendered reply can still misdescribe a
fact in prose that names no field at all (for example, paraphrasing an outcome without using
``OUTCOME_STATEMENT``) — the verifier's required-field check catches an omission, not a paraphrase
that avoids every field name while still misstating something.
"""

from __future__ import annotations

# Standard libraries
import unicodedata  # Control characters in free text
from collections.abc import Mapping  # Type of the template table
from datetime import date, datetime, timedelta  # Absolute dates and instants that must be UTC
from decimal import Decimal  # Money is never a float
from enum import StrEnum  # Closed sets of the contract
from typing import Annotated, Final, Literal  # Bounded fields, constants, closed sets

# Third-party libraries
from pydantic import (  # Validated immutable models
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

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

# The version string every envelope and customer response carries.
CONTRACT_VERSION: Final = "1"

# The conversation languages (Spanish, Portuguese, English) and the same set as a tuple.
Lang = Literal["es", "pt", "en"]
LANGUAGES: tuple[Lang, ...] = ("es", "pt", "en")

# A probability or score between 0 and 1 inclusive: confidence, risk score, interval bound, rate.
Rate = Annotated[float, Field(ge=0, le=1)]

# Identifier shapes: opaque references of at most 64 characters, and shorter case and ticket
# numbers; no spaces, so an identifier cannot carry a sentence.
REF_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
NUMBER_PATTERN = r"^[A-Za-z0-9_-]{1,32}$"


def _refuse_control_characters(value: str) -> str:
    """Refuse text holding a control character, which no customer-visible field needs.

    Detecting a card number hiding in free text is a redaction concern, not a contract shape:
    it belongs to the masking serializer, the egress boundary the PII-minimization invariant
    names, where it can be tested adversarially and measured for its false-positive rate in one
    place instead of duplicated across every free-text field here.
    """
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise ValueError("text must not contain control characters")
    return value


def _require_utc(value: datetime) -> datetime:
    """Refuse an instant that is not expressed in UTC."""
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("instants of record must be in UTC")
    return value


# Free text a system field may hold: no control characters.
SafeText = Annotated[str, AfterValidator(_refuse_control_characters)]

# An instant of record, always UTC.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]


class ContractModel(BaseModel):
    """Base of every contract model: immutable, and unknown fields are an error."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Intent(StrEnum):
    """What the reply is for."""

    # Ask for or restate something, or carry the conversation on (greeting, language offer).
    CLARIFY = "clarify"
    # List the matching transactions.
    PRESENT_TRANSACTIONS = "present_transactions"
    # Ask the customer to confirm filing one selected transaction.
    CONFIRM_FILING = "confirm_filing"
    # State the case that was filed, read back from the case service.
    FILING_RESULT = "filing_result"
    # State that the transaction cannot be disputed, and why in plain words.
    INELIGIBLE = "ineligible"
    # State where an existing case stands, or that none exists.
    DISPUTE_STATUS = "dispute_status"
    # Answer a policy question from cited policy sections.
    POLICY_ANSWER = "policy_answer"
    # Say the policy does not answer the question, citing nothing.
    ABSTAIN = "abstain"
    # Decline a request the system does not support.
    REFUSE = "refuse"
    # Tell the customer the matter passes to a person.
    HANDOFF = "handoff"
    # Close the conversation.
    FAREWELL = "farewell"


class DateSource(StrEnum):
    """How the customer expressed a date, which decides whether it is confirmed in words."""

    ABSOLUTE = "absolute"  # A complete date the customer stated outright.
    RELATIVE = "relative"  # Relative to the reference date: "yesterday", a weekday name.
    PARTIAL = "partial"  # A day of the month only, such as "dia 3".
    NUMERIC = "numeric"  # Day-first digits such as 03/04, which read differently month-first.


class Slot(StrEnum):
    """The element the conversation is waiting for."""

    TRANSACTION = "transaction"  # Which transaction the customer means.
    TRANSACTION_CHOICE = "transaction_choice"  # Which of the listed transactions.
    REASON = "reason"  # Why the customer disputes it, which gives the category.
    CONFIRMATION = "confirmation"  # Yes or no to filing.


class CustomerReason(StrEnum):
    """The plain reason a customer may be told; never a reason code.

    Every request routed to a person maps to ``NEEDS_REVIEW``, so a customer cannot tell which
    rule sent it there.
    """

    ELIGIBLE = "eligible"
    WINDOW_EXPIRED = "window_expired"  # Past the category's filing window.
    PENDING = "pending"  # The transaction is still pending.
    DECLINED = "declined"  # The transaction was declined.
    REVERSED = "reversed"  # The transaction was already reversed.
    DUPLICATE_CASE = "duplicate_case"  # An open case already exists for it.
    NOT_DISPUTABLE = "not_disputable"  # Product out of scope, type not disputable, future date.
    NEEDS_REVIEW = "needs_review"  # Routed to a person, whatever rule routed it.


class TemplateId(StrEnum):
    """Fixed-wording texts. Each has one wording per language in the renderer."""

    # Opening, and asking for each missing element.
    GREETING = "greeting"
    CLARIFY_TRANSACTION = "clarify_transaction"
    CLARIFY_REASON = "clarify_reason"
    CLARIFY_CHOICE = "clarify_choice"
    CLARIFY_CONFIRMATION = "clarify_confirmation"
    LANGUAGE_OFFER = "language_offer"
    # Showing transactions: one match, a list, a request to narrow several matches, or no match.
    PRESENT_ONE = "present_one"
    PRESENT_LIST = "present_list"
    PRESENT_NARROW = "present_narrow"
    NOT_FOUND = "not_found"
    # Filing: the confirmation prompt and the outcomes. ``FILING_UNVERIFIED`` is a filing whose
    # read-back failed, so it is handed to a person instead of reported as done.
    CONFIRM_FILING = "confirm_filing"
    FILING_RESULT = "filing_result"
    FILING_UNVERIFIED = "filing_unverified"
    FILING_CANCELLED = "filing_cancelled"
    # Ineligibility, and case status.
    INELIGIBLE = "ineligible"
    DISPUTE_STATUS = "dispute_status"
    NO_CASE_FOUND = "no_case_found"
    # Policy questions: an answer from cited sections, or an abstention.
    POLICY_ANSWER = "policy_answer"
    ABSTAIN_POLICY = "abstain_policy"
    # Refusals: an unsupported action, and a request to reverse a transaction.
    REFUSE_UNSUPPORTED = "refuse_unsupported"
    REFUSE_REVERSAL = "refuse_reversal"
    # Handoffs, one wording per reason: routed by a rule, a fraud report, a card loss, the
    # customer's own request, and a request that could not be registered.
    HANDOFF_REVIEW = "handoff_review"
    HANDOFF_FRAUD = "handoff_fraud"
    HANDOFF_CARD_LOSS = "handoff_card_loss"
    HANDOFF_REQUESTED = "handoff_requested"
    HANDOFF_NOT_REGISTERED = "handoff_not_registered"
    # Starting over after the previous session expired with nothing filed, and closing.
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

    name: Annotated[SafeText, Field(min_length=1, max_length=60)]
    last4: Annotated[str, Field(pattern=r"^\d{4}$")]


class TransactionFact(ContractModel):
    """One of the customer's transactions as the reply may describe it.

    An amount is absent only when the source data itself carries none and no same-day conversion
    exists (rare); a reply states that plainly rather than inventing a figure.
    """

    ref: Annotated[str, Field(pattern=REF_PATTERN)]
    occurred_on: date
    # Required key but nullable: the source often has no merchant.
    merchant: Annotated[SafeText, Field(min_length=1, max_length=80)] | None
    # Required key but nullable: absent when there is no figure to state.
    amount: Money | None
    product: ProductLabel
    status: TransactionStatus


class CaseFact(ContractModel):
    """One dispute case as the case service holds it."""

    case_number: Annotated[str, Field(pattern=NUMBER_PATTERN)]
    # The case status as the case service states it, kept as text for the reply to quote.
    status: Annotated[str, Field(min_length=1, max_length=32)]
    filed_on: date
    transaction_ref: Annotated[str, Field(pattern=REF_PATTERN)]
    expected_response_on: date | None = None

    @model_validator(mode="after")
    def _response_is_not_before_filing(self) -> CaseFact:
        """The first response is expected on or after the day the case was filed."""
        if self.expected_response_on is not None and self.expected_response_on < self.filed_on:
            raise ValueError("expected_response_on is before filed_on")
        return self


class WindowFact(ContractModel):
    """A filing window stated in days, with the deadline as a date."""

    # Length of the category's filing window, and the days elapsed since the transaction.
    days_allowed: Annotated[int, Field(ge=1)]
    age_days: Annotated[int, Field(ge=0)]
    # The last day to file: the domain date plus the days remaining (checked on the envelope).
    deadline: date


class PolicyValue(ContractModel):
    """One parameter of the policy in force, written as the policy states it.

    A list, such as the evidence a category needs, is written as its stable identifiers joined by
    commas; the renderer states each in the reply language.
    """

    name: Annotated[str, Field(min_length=1, max_length=64)]
    value: Annotated[str, Field(min_length=1, max_length=200)]


class DateToConfirm(ContractModel):
    """A date read from what the customer said, awaiting their confirmation in words."""

    resolved_on: date
    source: DateSource


class DisputeFacts(ContractModel):
    """Everything about the customer's situation a reply may state, masked and session-scoped."""

    # The transactions the reply may list (at most five), and how many matched in all.
    transactions: Annotated[tuple[TransactionFact, ...], Field(max_length=5)] = ()
    candidate_count: Annotated[int, Field(ge=0)] = 0
    # The reference of the transaction chosen for the dispute. A confirm-filing reply must pick
    # one of the transactions listed; other replies may carry a reference that is not listed.
    selected_ref: Annotated[str, Field(pattern=REF_PATTERN)] | None = None
    category: DisputeCategory | None = None
    # Cases on file that the reply may state (at most three).
    cases: Annotated[tuple[CaseFact, ...], Field(max_length=3)] = ()
    window: WindowFact | None = None
    # Policy parameters a policy answer may quote (at most sixteen).
    policy_values: Annotated[tuple[PolicyValue, ...], Field(max_length=16)] = ()
    # When the first response to a newly filed case is expected.
    expected_response_on: date | None = None
    # Search terms a reply may echo back (at most six); empty when none are set.
    search_terms: Annotated[
        tuple[Annotated[SafeText, Field(min_length=1, max_length=80)], ...], Field(max_length=6)
    ] = ()
    date_to_confirm: DateToConfirm | None = None
    # How many further disputes the customer has mentioned beyond the current one (at most five);
    # zero when none are set.
    pending_disputes: Annotated[int, Field(ge=0, le=5)] = 0
    # The contact promise made in a handoff, in hours, and the handoff ticket's number.
    contact_within_hours: Annotated[int, Field(ge=1)] | None = None
    ticket_ref: Annotated[str, Field(pattern=NUMBER_PATTERN)] | None = None

    @model_validator(mode="after")
    def _candidates_cover_the_transactions(self) -> DisputeFacts:
        """The count of matches is never below the transactions listed."""
        if self.candidate_count < len(self.transactions):
            raise ValueError("candidate_count is below the number of transactions listed")
        return self


# -----------------------------------------------------------------------------
# Decisions and sources
# -----------------------------------------------------------------------------

# The plain reason for each reason code. Exhaustive over ``ReasonCode``: a code added to the
# policy without an entry here fails the tests, so no decision can reach a customer unexplained.
CUSTOMER_REASON_OF: Mapping[ReasonCode, CustomerReason] = {
    ReasonCode.ELIGIBLE: CustomerReason.ELIGIBLE,
    ReasonCode.PRODUCT_OUT_OF_SCOPE: CustomerReason.NOT_DISPUTABLE,
    ReasonCode.TRANSACTION_TYPE_NOT_DISPUTABLE: CustomerReason.NOT_DISPUTABLE,
    ReasonCode.TRANSACTION_DECLINED: CustomerReason.DECLINED,
    ReasonCode.TRANSACTION_PENDING: CustomerReason.PENDING,
    ReasonCode.TRANSACTION_REVERSED: CustomerReason.REVERSED,
    ReasonCode.TRANSACTION_DATE_IN_FUTURE: CustomerReason.NOT_DISPUTABLE,
    ReasonCode.FILING_WINDOW_EXPIRED: CustomerReason.WINDOW_EXPIRED,
    ReasonCode.DUPLICATE_OPEN_CASE: CustomerReason.DUPLICATE_CASE,
    ReasonCode.ESCALATE_FRAUD_CLAIM: CustomerReason.NEEDS_REVIEW,
    ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE: CustomerReason.NEEDS_REVIEW,
    ReasonCode.ESCALATE_REPEAT_COMPLAINER: CustomerReason.NEEDS_REVIEW,
    ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD: CustomerReason.NEEDS_REVIEW,
    ReasonCode.ESCALATE_AMOUNT_UNKNOWN: CustomerReason.NEEDS_REVIEW,
    ReasonCode.ESCALATE_RISK_SCORE: CustomerReason.NEEDS_REVIEW,
}

# The plain reasons that pair with an ineligible outcome: every one but eligible and needs-review.
_INELIGIBLE_REASONS = frozenset(
    reason
    for reason in CustomerReason
    if reason not in {CustomerReason.ELIGIBLE, CustomerReason.NEEDS_REVIEW}
)


def outcome_of(reason_code: ReasonCode) -> Outcome:
    """The outcome a reason code belongs to, derived from its plain reason."""
    reason = CUSTOMER_REASON_OF[reason_code]
    if reason is CustomerReason.ELIGIBLE:
        return Outcome.ELIGIBLE
    if reason is CustomerReason.NEEDS_REVIEW:
        return Outcome.ESCALATE
    return Outcome.INELIGIBLE


class Decision(ContractModel):
    """The renderer's view of one policy decision: what was decided and the plain reason."""

    outcome: Outcome
    customer_reason: CustomerReason
    # The version of the policy the decision was made under.
    policy_version: Annotated[str, Field(min_length=1)]
    # Whether the customer must confirm before the case is filed; only an eligible decision can.
    requires_confirmation: bool = False

    @model_validator(mode="after")
    def _reason_agrees_with_outcome(self) -> Decision:
        """Eligible pairs with eligible, escalate with needs-review, ineligible with the rest."""
        expected = {
            Outcome.ELIGIBLE: frozenset({CustomerReason.ELIGIBLE}),
            Outcome.ESCALATE: frozenset({CustomerReason.NEEDS_REVIEW}),
            Outcome.INELIGIBLE: _INELIGIBLE_REASONS,
        }[self.outcome]
        if self.customer_reason not in expected:
            raise ValueError("customer_reason does not agree with outcome")
        if self.requires_confirmation and self.outcome is not Outcome.ELIGIBLE:
            raise ValueError("only an eligible decision can require confirmation")
        return self


class InputFact(ContractModel):
    """One input of a decision, written as text for the audit and the console."""

    # The name of the input and its value written as text.
    name: Annotated[str, Field(min_length=1, max_length=64)]
    value: Annotated[str, Field(min_length=1, max_length=64)]


class AgentDecision(ContractModel):
    """The detail of one decision that only an agent may see."""

    reason_code: ReasonCode
    # Every escalation trigger that applied, when the decision routed to a person.
    triggers: tuple[ReasonCode, ...] = ()
    # The inputs the decision used, written as text.
    inputs: tuple[InputFact, ...] = ()


class RiskEvidence(ContractModel):
    """A risk score with its uncertainty, the base rate it is read against, and the routing
    threshold it was compared to — the policy's own decision line, not merely the score alone,
    so a reader can see why a score did or did not route to a person, once risk routing is on."""

    # The model's risk score and the bounds of its uncertainty interval.
    score: Rate
    interval_low: Rate
    interval_high: Rate
    # The overall rate of the risky outcome, to read the score against.
    base_rate: Rate
    # The score at or above which the policy routes to a person.
    threshold: Rate

    @model_validator(mode="after")
    def _interval_brackets_the_score(self) -> RiskEvidence:
        """A score lies within its own interval."""
        if not self.interval_low <= self.score <= self.interval_high:
            raise ValueError("score must lie between interval_low and interval_high")
        return self


class AgentOnly(ContractModel):
    """Routing detail for the audit, the packet and the console; never for a reply."""

    # One entry per envelope decision, in the same order; empty when there is no detail.
    decisions: tuple[AgentDecision, ...] = ()
    # The understanding step's own confidence in the customer's message.
    nlu_confidence: Rate | None = None
    risk: RiskEvidence | None = None


class LocalizedTitle(ContractModel):
    """The readable title of a policy section in one language."""

    lang: Lang
    text: Annotated[SafeText, Field(min_length=1, max_length=120)]


class SourceRef(ContractModel):
    """A policy section that grounds a statement.

    A reply cites the title in its language; the section identifier stays in the decision record.
    """

    # The policy section identifier; stays in the decision record, never shown to the customer.
    section_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
    # One title per reply language.
    titles: tuple[LocalizedTitle, ...]
    # The version of the policy corpus the section was taken from.
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
# Fixed texts and the intents that carry them
# -----------------------------------------------------------------------------

# The intents each fixed text may carry. Exhaustive over ``TemplateId``. Clarification texts also
# serve the replies that ask the customer to continue (a greeting, a language offer, a cancelled
# filing), and a narrowing question is a clarification that lists nothing.
TEMPLATE_INTENTS: Mapping[TemplateId, frozenset[Intent]] = {
    TemplateId.GREETING: frozenset({Intent.CLARIFY}),
    TemplateId.CLARIFY_TRANSACTION: frozenset({Intent.CLARIFY}),
    TemplateId.CLARIFY_REASON: frozenset({Intent.CLARIFY}),
    TemplateId.CLARIFY_CHOICE: frozenset({Intent.CLARIFY}),
    TemplateId.CLARIFY_CONFIRMATION: frozenset({Intent.CLARIFY}),
    TemplateId.LANGUAGE_OFFER: frozenset({Intent.CLARIFY}),
    TemplateId.PRESENT_ONE: frozenset({Intent.PRESENT_TRANSACTIONS}),
    TemplateId.PRESENT_LIST: frozenset({Intent.PRESENT_TRANSACTIONS}),
    TemplateId.PRESENT_NARROW: frozenset({Intent.CLARIFY}),
    TemplateId.NOT_FOUND: frozenset({Intent.CLARIFY}),
    TemplateId.CONFIRM_FILING: frozenset({Intent.CONFIRM_FILING}),
    TemplateId.FILING_RESULT: frozenset({Intent.FILING_RESULT}),
    TemplateId.FILING_UNVERIFIED: frozenset({Intent.HANDOFF}),
    TemplateId.FILING_CANCELLED: frozenset({Intent.CLARIFY}),
    TemplateId.INELIGIBLE: frozenset({Intent.INELIGIBLE}),
    TemplateId.DISPUTE_STATUS: frozenset({Intent.DISPUTE_STATUS}),
    TemplateId.NO_CASE_FOUND: frozenset({Intent.DISPUTE_STATUS}),
    TemplateId.POLICY_ANSWER: frozenset({Intent.POLICY_ANSWER}),
    TemplateId.ABSTAIN_POLICY: frozenset({Intent.ABSTAIN}),
    TemplateId.REFUSE_UNSUPPORTED: frozenset({Intent.REFUSE}),
    TemplateId.REFUSE_REVERSAL: frozenset({Intent.REFUSE}),
    TemplateId.HANDOFF_REVIEW: frozenset({Intent.HANDOFF}),
    TemplateId.HANDOFF_FRAUD: frozenset({Intent.HANDOFF}),
    TemplateId.HANDOFF_CARD_LOSS: frozenset({Intent.HANDOFF}),
    TemplateId.HANDOFF_REQUESTED: frozenset({Intent.HANDOFF}),
    TemplateId.HANDOFF_NOT_REGISTERED: frozenset({Intent.HANDOFF}),
    TemplateId.RESTART_AFTER_PENDING: frozenset({Intent.CLARIFY}),
    TemplateId.FAREWELL: frozenset({Intent.FAREWELL}),
}

# Handoff texts that state a routing decision, and so need an escalate decision behind them.
_ROUTED_HANDOFFS = frozenset({TemplateId.HANDOFF_REVIEW, TemplateId.HANDOFF_FRAUD})


# -----------------------------------------------------------------------------
# Grounded fields (model-rendered path)
# -----------------------------------------------------------------------------


class GroundedField(StrEnum):
    """A value the model-rendered path may cite by name.

    The model-rendered reply names a field (``{{amount}}``); the renderer substitutes the actual
    value from ``facts``, ``decisions`` or ``sources`` before the customer sees it, and the output
    verifier rejects a reply that writes a digit itself instead of naming a field. The model never
    sees the substituted value, only the closed set of names it may reference for the envelope's
    intent.
    """

    AMOUNT = "amount"
    OCCURRED_ON = "occurred_on"
    MERCHANT = "merchant"
    CASE_NUMBER = "case_number"
    CASE_STATUS = "case_status"
    FILED_ON = "filed_on"
    EXPECTED_RESPONSE_ON = "expected_response_on"
    TICKET_REF = "ticket_ref"
    CONTACT_WITHIN_HOURS = "contact_within_hours"
    CATEGORY = "category"
    SOURCE_TITLE = "source_title"
    POLICY_VALUE = "policy_value"
    OUTCOME_STATEMENT = "outcome_statement"


# The fields a model-rendered reply may name for each intent. Exhaustive over ``Intent``, checked
# for completeness against the enumeration the same way ``TEMPLATE_INTENTS`` is. ``Intent.REFUSE``
# carries an empty set: a refusal always renders from the fixed-wording template path (see
# ``_wording_matches_the_mode`` below), so no model-rendered reply ever reaches this intent.
INTENT_ALLOWED_FIELDS: Mapping[Intent, frozenset[GroundedField]] = {
    Intent.CLARIFY: frozenset(),
    Intent.PRESENT_TRANSACTIONS: frozenset(
        {GroundedField.AMOUNT, GroundedField.OCCURRED_ON, GroundedField.MERCHANT}
    ),
    Intent.CONFIRM_FILING: frozenset(
        {GroundedField.AMOUNT, GroundedField.OCCURRED_ON, GroundedField.CATEGORY}
    ),
    Intent.FILING_RESULT: frozenset(
        {GroundedField.CASE_NUMBER, GroundedField.EXPECTED_RESPONSE_ON}
    ),
    Intent.INELIGIBLE: frozenset({GroundedField.OUTCOME_STATEMENT}),
    Intent.DISPUTE_STATUS: frozenset(
        {
            GroundedField.CASE_NUMBER,
            GroundedField.CASE_STATUS,
            GroundedField.FILED_ON,
            GroundedField.OUTCOME_STATEMENT,
        }
    ),
    Intent.POLICY_ANSWER: frozenset({GroundedField.SOURCE_TITLE, GroundedField.POLICY_VALUE}),
    Intent.ABSTAIN: frozenset(),
    Intent.REFUSE: frozenset(),
    Intent.HANDOFF: frozenset(
        {
            GroundedField.TICKET_REF,
            GroundedField.CONTACT_WITHIN_HOURS,
            GroundedField.OUTCOME_STATEMENT,
        }
    ),
    Intent.FAREWELL: frozenset(),
}

# The fields a model-rendered reply for the intent must name at least once, so the model cannot
# silently drop the grounded content the intent exists to state (decision consistency: an outcome
# an intent commits to is never left unsaid). A subset of ``INTENT_ALLOWED_FIELDS`` for every
# intent; the fields listed here are exactly the ones every envelope of that intent's own
# validators (``_intent_has_what_it_states``, ``_handoff_states_what_happened``) guarantee are
# always available, so requiring them can never fail for want of a value to substitute.
# ``DISPUTE_STATUS`` and ``HANDOFF`` require only the outcome statement, the same as
# ``INELIGIBLE``: their case and ticket fields are conditional on facts a "no case"/"not
# registered" envelope legitimately lacks, so only the field every state of the intent always
# carries can be mandatory — the case-specific fields stay allowed, not required.
INTENT_REQUIRED_FIELDS: Mapping[Intent, frozenset[GroundedField]] = {
    Intent.CLARIFY: frozenset(),
    Intent.PRESENT_TRANSACTIONS: frozenset(),
    Intent.CONFIRM_FILING: frozenset(
        {GroundedField.AMOUNT, GroundedField.OCCURRED_ON, GroundedField.CATEGORY}
    ),
    Intent.FILING_RESULT: frozenset({GroundedField.CASE_NUMBER}),
    Intent.INELIGIBLE: frozenset({GroundedField.OUTCOME_STATEMENT}),
    Intent.DISPUTE_STATUS: frozenset({GroundedField.OUTCOME_STATEMENT}),
    Intent.POLICY_ANSWER: frozenset({GroundedField.SOURCE_TITLE}),
    Intent.ABSTAIN: frozenset(),
    Intent.REFUSE: frozenset(),
    Intent.HANDOFF: frozenset({GroundedField.OUTCOME_STATEMENT}),
    Intent.FAREWELL: frozenset(),
}


# -----------------------------------------------------------------------------
# Envelope
# -----------------------------------------------------------------------------


class _EnvelopeBody(ContractModel):
    """The content both views of an envelope share, and the rules every envelope satisfies."""

    contract_version: Literal["1"] = CONTRACT_VERSION
    session_id: Annotated[str, Field(min_length=1, max_length=64)]
    # The language the reply is rendered in.
    lang: Lang
    # The date the facts and the filing window are reckoned against (not the real clock).
    domain_date: date
    intent: Intent
    # The element the conversation waits for next, when the reply asks for one.
    next_expected: Slot | None = None
    # Whether this reply closes the session; true exactly for a farewell and a handoff.
    end_session: bool = False
    facts: DisputeFacts = DisputeFacts()
    decisions: tuple[Decision, ...] = ()
    sources: tuple[SourceRef, ...] = ()
    # "template" renders a fixed text named by ``template_id``; "model" lets the model word the
    # reply from the grounded fields allowed for the intent.
    render_mode: Literal["template", "model"] = "template"
    template_id: TemplateId | None = None

    @model_validator(mode="after")
    def _wording_matches_the_mode(self) -> _EnvelopeBody:
        """Template mode names its text, which belongs to the intent; model mode never names one."""
        if self.render_mode == "model":
            if self.template_id is not None:
                raise ValueError("model mode must not carry a template_id")
            if self.intent is Intent.REFUSE:
                raise ValueError("a refusal always renders from the fixed-wording template path")
        elif self.template_id is None:
            raise ValueError("template mode requires a template_id")
        elif self.intent not in TEMPLATE_INTENTS[self.template_id]:
            raise ValueError("template_id does not belong to the intent")
        return self

    @model_validator(mode="after")
    def _intent_has_what_it_states(self) -> _EnvelopeBody:
        """An intent that states something carries the material for it, and no more than it."""
        facts = self.facts
        outcomes = {decision.outcome for decision in self.decisions}
        if self.intent is Intent.PRESENT_TRANSACTIONS and not facts.transactions:
            raise ValueError("present_transactions requires at least one transaction")
        if self.intent is Intent.CONFIRM_FILING:
            listed = {transaction.ref for transaction in facts.transactions}
            if not (
                outcomes == {Outcome.ELIGIBLE}
                and any(d.requires_confirmation for d in self.decisions)
                and facts.selected_ref in listed
                and facts.category
            ):
                raise ValueError(
                    "confirm_filing requires only eligible decisions, one that needs "
                    "confirmation, a selected transaction among those listed and a category"
                )
        if self.intent is Intent.FILING_RESULT and not facts.cases:
            raise ValueError("filing_result requires the case read back")
        if self.intent is Intent.INELIGIBLE and outcomes != {Outcome.INELIGIBLE}:
            raise ValueError("ineligible requires only ineligible decisions")
        if (
            self.intent is Intent.DISPUTE_STATUS
            and not facts.cases
            and self.render_mode == "template"
            and self.template_id is not TemplateId.NO_CASE_FOUND
        ):
            raise ValueError("dispute_status requires cases unless it states there are none")
        if self.intent is Intent.POLICY_ANSWER and not self.sources:
            raise ValueError("policy_answer requires at least one source")
        if self.intent is Intent.ABSTAIN and (self.sources or facts.policy_values):
            raise ValueError("abstain carries no source and no policy value")
        return self

    @model_validator(mode="after")
    def _ending_follows_the_intent(self) -> _EnvelopeBody:
        """A farewell and a handoff end the session, and nothing else does."""
        ends = self.intent in {Intent.FAREWELL, Intent.HANDOFF}
        if self.end_session != ends:
            raise ValueError("exactly farewell and handoff end a session")
        return self

    @model_validator(mode="after")
    def _handoff_states_what_happened(self) -> _EnvelopeBody:
        """A handoff names its ticket unless nothing was registered, and never reads as eligible."""
        if self.intent is not Intent.HANDOFF:
            return self
        if self.render_mode == "template":
            registered = self.template_id is not TemplateId.HANDOFF_NOT_REGISTERED
            if registered != (self.facts.ticket_ref is not None):
                raise ValueError(
                    "a handoff names its ticket exactly when the request was registered"
                )
        if any(decision.outcome is Outcome.ELIGIBLE for decision in self.decisions):
            raise ValueError("a handoff carries no eligible decision")
        if self.template_id in _ROUTED_HANDOFFS and not any(
            decision.outcome is Outcome.ESCALATE for decision in self.decisions
        ):
            raise ValueError("this handoff text requires an escalate decision")
        return self

    @model_validator(mode="after")
    def _window_deadline_matches_domain_date(self) -> _EnvelopeBody:
        """The filing deadline is the domain date plus the days remaining in the window."""
        window = self.facts.window
        if window is not None:
            expected = self.domain_date + timedelta(days=window.days_allowed - window.age_days)
            if window.deadline != expected:
                raise ValueError(
                    "window.deadline does not match domain_date, days_allowed and age_days"
                )
        return self


class RenderEnvelope(_EnvelopeBody):
    """What the renderer receives: the envelope without the agent-only detail."""


class Envelope(_EnvelopeBody):
    """The full envelope: the shared content plus the agent-only detail.

    It is a sibling of ``RenderEnvelope``, not a subclass, so it is never accepted where the
    renderer's view is required.
    """

    agent_only: AgentOnly | None = None

    @model_validator(mode="after")
    def _agent_detail_pairs_with_the_decisions(self) -> Envelope:
        """Each agent decision belongs to the decision at its index and gives its plain reason."""
        if self.agent_only is None or not self.agent_only.decisions:
            return self
        if len(self.agent_only.decisions) != len(self.decisions):
            raise ValueError("agent_only.decisions must pair one to one with decisions")
        # The reason code alone decides the outcome (``outcome_of``), so matching the plain
        # reason is enough to guarantee the outcome matches too.
        for agent, decision in zip(self.agent_only.decisions, self.decisions, strict=True):
            if CUSTOMER_REASON_OF[agent.reason_code] is not decision.customer_reason:
                raise ValueError("an agent reason code does not match its decision")
        return self

    def render_view(self) -> RenderEnvelope:
        """The same envelope without ``agent_only``, the only form a renderer receives."""
        return RenderEnvelope(**{name: getattr(self, name) for name in _EnvelopeBody.model_fields})


# -----------------------------------------------------------------------------
# Tool failure
# -----------------------------------------------------------------------------


class ToolUnavailable(ContractModel):
    """The typed result of a tool that cannot answer; the controller turns it into a handoff."""

    # The name of the tool that could not answer.
    tool: Annotated[str, Field(min_length=1, max_length=64)]
    cause: Literal["timeout", "error", "circuit_open"]
    retryable: bool = False
