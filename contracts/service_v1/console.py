"""
Human-Agent Console Contract, Service Version 1
===============================================

Overview
--------
What the console shows an agent: the queue of open handoff tickets, one ticket with its whole
packet, and the audit timeline of the conversation behind it. Also the narrow agent writes:
claiming or releasing a ticket, adding a note, and setting a case's status.

Scope
-----
In: the queue item, filters, ticket detail, timeline entry, and the narrow write requests/results
(``ClaimTicketResult``, ``AddNoteRequest``/``Note``, ``SetCaseStatusRequest``/``CaseStatusResult``).
Out: the agent routes, the agent sign-in and the repositories behind them.

Design Principles
-----------------
- Agent-only material lives here and nowhere a customer token can reach: the trigger, the reason
  codes, the routing inputs and the risk evidence.
- Both clocks are shown and labeled: the reference date and the real UTC instant.
- The timeline holds decisions and reasons, never message text; the customer is a first name and
  a masked identifier.
- Fraud and card-loss tickets sort first: the queue carries the flag that says so, and the flag
  cannot disagree with the trigger.
- A ticket's row and its packet agree, and every instant is UTC.
- A claim is orthogonal to a ticket's own lifecycle status: ``claimed_by`` names who is currently
  working a ticket, ``status`` names where it stands; the two never conflate.
- A note names its own author and instant, the same identity and timing every audited write
  carries; it is never edited or removed once added.

Runtime Contract
----------------
``QueueResponse`` of ``QueueItem``, filtered by ``QueueFilters``; ``TicketDetail`` with its
``TimelineEntry`` records and ``Note`` records; ``ClaimTicketResult``; ``AddNoteRequest``,
``Note``; ``SetCaseStatusRequest``, ``CaseStatusResult``.
"""

from __future__ import annotations

# Standard libraries
from datetime import date  # Reference dates
from enum import StrEnum  # Closed set of ticket statuses
from typing import Annotated, Literal  # Bounded fields and closed values

# Third-party libraries
from pydantic import Field, model_validator  # Field bounds and cross-field rules

# Local modules
from app.domain.policy.models import DisputeCategory, ReasonCode  # Shared vocabulary
from contracts.service_v1.api import ReferenceDateOrigin  # Origin of the reference date
from contracts.service_v1.cases import CaseStatus  # The status a narrow write may set
from contracts.service_v1.envelope import (  # Shared base and types
    NUMBER_PATTERN,
    ContractModel,
    Intent,
    Lang,
    SafeText,
    UtcDatetime,
)
from contracts.service_v1.handoff import HandoffPacket, HandoffTrigger  # The packet

# An agent's durable identifier: letters, digits, underscore and hyphen, at most 20 characters.
_AGENT_ID_PATTERN = r"^[A-Za-z0-9_-]{1,20}$"

# The triggers that sort a ticket first in the queue.
_PRIORITY_TRIGGERS = frozenset({HandoffTrigger.FRAUD_REPORT, HandoffTrigger.CARD_LOSS})


class TicketStatus(StrEnum):
    """Where a handoff ticket stands."""

    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class QueueFilters(ContractModel):
    """The filters a queue request may carry."""

    # Each filter is optional; a filter left unset does not narrow the queue.
    language: Lang | None = None
    trigger: HandoffTrigger | None = None


class QueueItem(ContractModel):
    """One row of the queue."""

    ticket_ref: Annotated[str, Field(pattern=NUMBER_PATTERN)]
    trigger: HandoffTrigger
    language: Lang
    category: DisputeCategory | None = None
    status: TicketStatus
    # The real UTC instant the ticket was created, and the reference date in force then.
    created_at: UtcDatetime
    reference_date: date
    # The date by which the customer was promised contact.
    promised_contact_by: date
    # Days from the ticket's reference date to the queue's reference date.
    age_days: Annotated[int, Field(ge=0)]
    # True exactly for a fraud or card-loss ticket (checked against ``trigger``).
    priority: bool
    # The agent currently working the ticket, or ``None`` when it is unclaimed.
    claimed_by: Annotated[str, Field(pattern=_AGENT_ID_PATTERN)] | None = None

    @model_validator(mode="after")
    def _priority_follows_the_trigger(self) -> QueueItem:
        """A fraud or card-loss ticket is priority and no other ticket is."""
        if self.priority != is_priority(self.trigger):
            raise ValueError("priority must be true exactly for fraud and card-loss triggers")
        return self


class QueueResponse(ContractModel):
    """The queue, priority tickets first."""

    # The reference date the queue was computed at, and where that date came from.
    reference_date: date
    reference_date_origin: ReferenceDateOrigin
    items: tuple[QueueItem, ...]


class TimelineEntry(ContractModel):
    """One audited step of the conversation, without any message text."""

    occurred_at: UtcDatetime
    trace_id: Annotated[str, Field(min_length=1, max_length=64)]
    # The turn this entry records. Every entry of one conversation shares its trace identifier, so
    # this is what tells two entries apart; it is unique within a conversation.
    turn_id: Annotated[str, Field(min_length=1, max_length=64)]
    # The intent of the reply sent, and the conversation state before and after the step.
    intent: Intent
    state_before: Annotated[str, Field(min_length=1, max_length=48)]
    state_after: Annotated[str, Field(min_length=1, max_length=48)]
    render_mode: Literal["template", "model"]
    # Set when a policy decision produced the step.
    reason_code: ReasonCode | None = None
    policy_version: Annotated[str, Field(min_length=1)] | None = None


class Note(ContractModel):
    """One agent's note on a ticket, in the order it was added. Never edited or removed."""

    agent_id: Annotated[str, Field(pattern=_AGENT_ID_PATTERN)]
    note_text: Annotated[SafeText, Field(min_length=1, max_length=500)]
    created_at: UtcDatetime


class TicketDetail(ContractModel):
    """One ticket: its queue row, its whole packet, the timeline behind it and its own notes."""

    item: QueueItem
    packet: HandoffPacket
    # The audited steps of the conversation behind the ticket.
    timeline: tuple[TimelineEntry, ...]
    # The agents' notes, in the order they were added.
    notes: tuple[Note, ...] = ()

    @model_validator(mode="after")
    def _row_describes_the_packet(self) -> TicketDetail:
        """The queue row and the packet of one ticket agree on what they both state."""
        row, packet = self.item, self.packet
        pairs = (
            (row.ticket_ref, packet.ticket_ref),
            (row.trigger, packet.trigger),
            (row.language, packet.language),
            (row.category, packet.category),
            (row.reference_date, packet.reference_date),
            (row.created_at, packet.created_at),
        )
        if any(shown != held for shown, held in pairs):
            raise ValueError("the queue item does not describe the packet")
        return self


class ClaimTicketResult(ContractModel):
    """A ticket's claim state after a claim or release — the updated queue row."""

    item: QueueItem


class AddNoteRequest(ContractModel):
    """The text of a new note; the author and instant come from the agent's own session."""

    note_text: Annotated[SafeText, Field(min_length=1, max_length=500)]


class SetCaseStatusRequest(ContractModel):
    """The status a narrow agent write asks to set a case to."""

    status: CaseStatus


class CaseStatusResult(ContractModel):
    """A case's status after a narrow agent write set it."""

    case_number: Annotated[str, Field(pattern=NUMBER_PATTERN)]
    status: CaseStatus


def is_priority(trigger: HandoffTrigger) -> bool:
    """Whether a ticket with ``trigger`` sorts first in the queue."""
    return trigger in _PRIORITY_TRIGGERS
