"""
Handoff Packet Contract, Service Version 1
==========================================

Overview
--------
The structured packet a person receives when a conversation is handed over: what the customer
wants, what was verified, what the system did or refused, the evidence behind the routing and
what is still open. It lets an agent act without reading the conversation and without asking the
customer to repeat anything.

Scope
-----
In: the packet, its parts and the closed set of triggers.
Out: building it (the handoff builder), storing it (the outbox) and showing it (the console).

Design Principles
-----------------
- Complete where it can be: the ticket, the trigger, the customer, the language, both clocks, the
  request summary and the evidence (with its policy version) are required. The verified facts,
  the actions, the open questions, the reason codes and the sources may be empty, because a
  customer's own request for a person, or a tool failure, has none of them.
- No raw material. There is no field for a transcript, a message text, a document number or a
  full card number; the customer appears as a first name and a masked identifier, and the free
  text is a bounded description in the system's words.
- Two clocks, both labeled: the reference date the packet used and the real UTC instant of the
  handoff.
- The language is a field of its own and a flag, because only some agents read Portuguese.

Runtime Contract
----------------
``HandoffPacket`` with ``HandoffTrigger``, ``ActionRecord``, ``Evidence`` and ``OpenQuestion``.

Limitations
-----------
The risk evidence and the reason codes are for agents only; a packet is never returned to a
customer token.
"""

from __future__ import annotations

# Standard libraries
from datetime import date  # The reference date
from enum import StrEnum  # Closed sets of the contract
from typing import Annotated  # Bounded fields

# Third-party libraries
from pydantic import Field, model_validator  # Field bounds and cross-field rules

# Local modules
from app.domain.policy.models import DisputeCategory, ReasonCode  # Shared vocabulary
from contracts.service_v1.envelope import (  # Shared base and typed parts
    NUMBER_PATTERN,
    ContractModel,
    Lang,
    RiskEvidence,
    SafeText,
    Slot,
    SourceRef,
    TransactionFact,
    UtcDatetime,
)


class HandoffTrigger(StrEnum):
    """Why the conversation was handed to a person."""

    FRAUD_REPORT = "fraud_report"  # The customer reports fraud.
    CARD_LOSS = "card_loss"  # The customer reports a lost or stolen card.
    CUSTOMER_REQUEST = "customer_request"  # The customer asked for a person.
    AMOUNT_REVIEW = "amount_review"  # The amount is at or above the review threshold.
    REPEAT_COMPLAINER = "repeat_complainer"  # The customer has filed repeatedly.
    RISK_SCORE = "risk_score"  # The risk score is at or above the routing threshold.
    AMOUNT_UNKNOWN = "amount_unknown"  # No figure exists to compare to the threshold.
    LOW_UNDERSTANDING = "low_understanding"  # The request stayed unclear after the allowed asks.
    TOOL_FAILURE = "tool_failure"  # A tool could not answer.
    FILING_UNVERIFIED = "filing_unverified"  # A filing could not be confirmed on read-back.


class ActionRecord(ContractModel):
    """One action the system took or refused, in its own words."""

    # What was done or attempted, and how it ended, as short system-worded labels.
    action: Annotated[SafeText, Field(min_length=1, max_length=64)]
    result: Annotated[SafeText, Field(min_length=1, max_length=64)]


class Evidence(ContractModel):
    """What the routing rests on."""

    # Every reason code behind the routing; empty when no rule routed (a request for a person).
    reason_codes: tuple[ReasonCode, ...]
    policy_version: Annotated[str, Field(min_length=1)]
    # Policy sections that ground the routing, if any.
    sources: tuple[SourceRef, ...] = ()
    risk: RiskEvidence | None = None


class OpenQuestion(ContractModel):
    """An element still missing, and how many times the customer was asked for it."""

    slot: Slot
    # How many times the customer was asked for it.
    attempts: Annotated[int, Field(ge=0)]


class CustomerLabel(ContractModel):
    """The customer as an agent may see them: a first name and a masked identifier."""

    first_name: Annotated[SafeText, Field(min_length=1, max_length=40)]
    # Four asterisks followed by the last two to four characters of the identifier.
    masked_id: Annotated[str, Field(pattern=r"^\*{4}[A-Za-z0-9]{2,4}$")]


class HandoffPacket(ContractModel):
    """The packet of one handoff."""

    ticket_ref: Annotated[str, Field(pattern=NUMBER_PATTERN)]
    # The reference date the packet used, and the real UTC instant of the handoff.
    reference_date: date
    created_at: UtcDatetime
    language: Lang
    # True exactly when the language is not Spanish, so the console can route to a matching agent.
    needs_language_routing: bool
    trigger: HandoffTrigger
    customer: CustomerLabel
    category: DisputeCategory | None = None
    # A bounded description in the system's words, never the customer's own message.
    request_summary: Annotated[SafeText, Field(min_length=1, max_length=300)]
    # The transactions the system verified against the records.
    verified_facts: tuple[TransactionFact, ...] = ()
    # What the system did, and the one action it tried but did not complete.
    actions: tuple[ActionRecord, ...] = ()
    attempted_action: ActionRecord | None = None
    # A case already on file for the transaction, if there is one.
    existing_case_number: Annotated[str, Field(pattern=NUMBER_PATTERN)] | None = None
    evidence: Evidence
    open_questions: tuple[OpenQuestion, ...] = ()

    @model_validator(mode="after")
    def _language_flag_follows_the_language(self) -> HandoffPacket:
        """Portuguese and English flag the packet so the console can route it."""
        if self.needs_language_routing != (self.language != "es"):
            raise ValueError("needs_language_routing must be true exactly when language is not es")
        return self
