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
- Complete by construction: the request, the verified facts, the actions, the evidence and the
  open questions are required parts, so an incomplete packet cannot be built.
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
from pydantic import AwareDatetime, Field, model_validator  # Field bounds and time-zone-aware time

# Local modules
from app.domain.policy.models import DisputeCategory, ReasonCode  # Shared vocabulary
from contracts.service_v1.envelope import (  # Shared base and typed parts
    ContractModel,
    Lang,
    RiskEvidence,
    Slot,
    SourceRef,
    TransactionFact,
)


class HandoffTrigger(StrEnum):
    """Why the conversation was handed to a person."""

    FRAUD_REPORT = "fraud_report"
    CARD_LOSS = "card_loss"
    CUSTOMER_REQUEST = "customer_request"
    AMOUNT_REVIEW = "amount_review"
    REPEAT_COMPLAINER = "repeat_complainer"
    RISK_SCORE = "risk_score"
    AMOUNT_UNKNOWN = "amount_unknown"
    LOW_UNDERSTANDING = "low_understanding"
    TOOL_FAILURE = "tool_failure"
    FILING_UNVERIFIED = "filing_unverified"


class ActionRecord(ContractModel):
    """One action the system took or refused, in its own words."""

    action: Annotated[str, Field(min_length=1, max_length=64)]
    result: Annotated[str, Field(min_length=1, max_length=64)]


class Evidence(ContractModel):
    """What the routing rests on."""

    reason_codes: tuple[ReasonCode, ...]
    policy_version: Annotated[str, Field(min_length=1)]
    sources: tuple[SourceRef, ...] = ()
    risk: RiskEvidence | None = None


class OpenQuestion(ContractModel):
    """An element still missing, and how many times the customer was asked for it."""

    slot: Slot
    attempts: Annotated[int, Field(ge=0)]


class CustomerLabel(ContractModel):
    """The customer as an agent may see them: a first name and a masked identifier."""

    first_name: Annotated[str, Field(min_length=1, max_length=40)]
    masked_id: Annotated[str, Field(pattern=r"^\*{4}[A-Za-z0-9]{2,4}$")]


class HandoffPacket(ContractModel):
    """The packet of one handoff."""

    ticket_ref: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")]
    reference_date: date
    created_at: AwareDatetime
    language: Lang
    needs_language_routing: bool
    trigger: HandoffTrigger
    customer: CustomerLabel
    category: DisputeCategory | None = None
    request_summary: Annotated[str, Field(min_length=1, max_length=300)]
    verified_facts: tuple[TransactionFact, ...] = ()
    actions: tuple[ActionRecord, ...] = ()
    attempted_action: ActionRecord | None = None
    existing_case_number: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")] | None = None
    evidence: Evidence
    open_questions: tuple[OpenQuestion, ...] = ()

    @model_validator(mode="after")
    def _language_flag_follows_the_language(self) -> HandoffPacket:
        """Portuguese and English flag the packet so the console can route it."""
        if self.needs_language_routing != (self.language != "es"):
            raise ValueError("needs_language_routing must be true exactly when language is not es")
        return self

    @model_validator(mode="after")
    def _created_at_is_utc(self) -> HandoffPacket:
        """The real instant is stored in UTC, as every timestamp of record is."""
        offset = self.created_at.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("created_at must be in UTC")
        return self
