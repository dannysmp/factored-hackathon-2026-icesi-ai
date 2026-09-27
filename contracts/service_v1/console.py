"""
Human-Agent Console Contract, Service Version 1
===============================================

Overview
--------
What the read-only console shows an agent: the queue of open handoff tickets, one ticket with its
whole packet, and the audit timeline of the conversation behind it. The console is a viewer.

Scope
-----
In: the queue item, filters, ticket detail and timeline entry.
Out: the agent routes, the agent sign-in and the repository behind them.

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

Runtime Contract
----------------
``QueueResponse`` of ``QueueItem``, filtered by ``QueueFilters``; ``TicketDetail`` with its
``TimelineEntry`` records.

Limitations
-----------
There are no write models: claiming, releasing, noting and setting a status are not part of this
version.
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
from contracts.service_v1.envelope import (  # Shared base and types
    NUMBER_PATTERN,
    ContractModel,
    Intent,
    Lang,
    UtcDatetime,
)
from contracts.service_v1.handoff import HandoffPacket, HandoffTrigger  # The packet

_PRIORITY_TRIGGERS = frozenset({HandoffTrigger.FRAUD_REPORT, HandoffTrigger.CARD_LOSS})


class TicketStatus(StrEnum):
    """Where a handoff ticket stands."""

    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class QueueFilters(ContractModel):
    """The filters a queue request may carry."""

    language: Lang | None = None
    trigger: HandoffTrigger | None = None


class QueueItem(ContractModel):
    """One row of the queue."""

    ticket_ref: Annotated[str, Field(pattern=NUMBER_PATTERN)]
    trigger: HandoffTrigger
    language: Lang
    category: DisputeCategory | None = None
    status: TicketStatus
    created_at: UtcDatetime
    reference_date: date
    promised_contact_by: date
    age_days: Annotated[int, Field(ge=0)]
    priority: bool

    @model_validator(mode="after")
    def _priority_follows_the_trigger(self) -> QueueItem:
        """A fraud or card-loss ticket is priority and no other ticket is."""
        if self.priority != is_priority(self.trigger):
            raise ValueError("priority must be true exactly for fraud and card-loss triggers")
        return self


class QueueResponse(ContractModel):
    """The queue, priority tickets first."""

    reference_date: date
    reference_date_origin: ReferenceDateOrigin
    items: tuple[QueueItem, ...]


class TimelineEntry(ContractModel):
    """One audited step of the conversation, without any message text."""

    occurred_at: UtcDatetime
    trace_id: Annotated[str, Field(min_length=1, max_length=64)]
    intent: Intent
    state_before: Annotated[str, Field(min_length=1, max_length=48)]
    state_after: Annotated[str, Field(min_length=1, max_length=48)]
    render_mode: Literal["template", "model"]
    reason_code: ReasonCode | None = None
    policy_version: Annotated[str, Field(min_length=1)] | None = None


class TicketDetail(ContractModel):
    """One ticket: its queue row, its whole packet and the timeline behind it."""

    item: QueueItem
    packet: HandoffPacket
    timeline: tuple[TimelineEntry, ...]

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


def is_priority(trigger: HandoffTrigger) -> bool:
    """Whether a ticket with ``trigger`` sorts first in the queue."""
    return trigger in _PRIORITY_TRIGGERS
