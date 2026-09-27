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
- Fraud and card-loss tickets sort first: the queue carries the flag that says so.

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
from typing import Annotated  # Bounded fields

# Third-party libraries
from pydantic import AwareDatetime, Field  # Field bounds and time-zone-aware time

# Local modules
from app.domain.policy.models import DisputeCategory, ReasonCode  # Shared vocabulary
from contracts.service_v1.api import ReferenceDateOrigin  # Origin of the reference date
from contracts.service_v1.envelope import ContractModel, Intent, Lang  # Shared base and types
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

    ticket_ref: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")]
    trigger: HandoffTrigger
    language: Lang
    category: DisputeCategory | None = None
    status: TicketStatus
    created_at: AwareDatetime
    reference_date: date
    promised_contact_by: date
    age_days: Annotated[int, Field(ge=0)]
    priority: bool


class QueueResponse(ContractModel):
    """The queue, priority tickets first."""

    reference_date: date
    reference_date_origin: ReferenceDateOrigin
    items: tuple[QueueItem, ...]


class TimelineEntry(ContractModel):
    """One audited step of the conversation, without any message text."""

    occurred_at: AwareDatetime
    trace_id: Annotated[str, Field(min_length=1, max_length=64)]
    intent: Intent
    state_before: Annotated[str, Field(min_length=1, max_length=48)]
    state_after: Annotated[str, Field(min_length=1, max_length=48)]
    render_mode: Annotated[str, Field(pattern=r"^(template|model)$")]
    reason_code: ReasonCode | None = None
    policy_version: Annotated[str, Field(min_length=1)] | None = None


class TicketDetail(ContractModel):
    """One ticket: its queue row, its whole packet and the timeline behind it."""

    item: QueueItem
    packet: HandoffPacket
    timeline: tuple[TimelineEntry, ...]


def is_priority(trigger: HandoffTrigger) -> bool:
    """Whether a ticket with ``trigger`` sorts first in the queue."""
    return trigger in _PRIORITY_TRIGGERS
