"""
Postgres Handoff Queue
=======================

Overview
--------
The read side of the handoff outbox the human-agent console needs (ADR-17): the queue of tickets
still needing an agent's attention (a resolved or rejected ticket never appears), filtered by
language and trigger, fraud and card-loss tickets sorted first. Backs
``contracts/service_v1/console.py``'s ``QueueResponse``/``QueueItem``.

Scope
-----
In: listing and filtering ``handoff_outbox`` rows into ``QueueItem``s, computing each one's age and
promised contact date against a supplied domain calendar.
Out: one ticket's full packet and timeline (``app.persistence.ticket_detail``, needing live
transaction re-resolution and corpus source titles this module never touches), the console's own
routes and the audit-of-agent-reads write they must perform (ADR-17), writing to the outbox at all
(``app.persistence.handoff_outbox``).

Design Principles
-----------------
- **The promised contact time is keyed by trigger, never by dispute category:** it is a separate,
  synthetic configuration value with its own provenance, for a handoff ticket's own lifecycle,
  distinct from
  ``Policy.first_response_days``, which promises a response to a *filed dispute* — a different
  event a handoff ticket, by construction, never reaches. Keying on ``trigger`` also
  means a categoryless fraud-report or card-loss ticket (reachable in practice — a customer can
  report either before any dispute category is ever established) needs no special case: the
  priority flag and the promised date are derived from the same input and can never disagree.
- **The age is measured on the domain calendar, not the real clock.** ``created_at`` (the real UTC
  instant) is reported alongside it, unchanged, so the console can show both, labeled, as ADR-17
  requires — this module only computes the domain-date age.
- **One connection per call**, matching every other module in ``app.persistence``.

Runtime Contract
----------------
``PostgresHandoffQueue(dsn, *, contact_days_priority, contact_days_default)`` with
``list_tickets(filters, *, calendar) -> QueueResponse`` and
``get_ticket(ticket_ref, *, calendar) -> QueueItem | None`` (unlike ``list_tickets``, not filtered
by status — a resolved or rejected ticket a console session already had selected must still
resolve, AC-E10-08).
"""

from __future__ import annotations

# Standard libraries
from datetime import date, timedelta  # Promised contact date, ticket age
from typing import Any  # Raw driver rows

# Third-party libraries
import psycopg  # Serving-store driver

# Local modules
from app.domain.calendar import DomainCalendar
from app.domain.policy.models import DisputeCategory
from contracts.service_v1.api import ReferenceDateOrigin
from contracts.service_v1.console import (
    QueueFilters,
    QueueItem,
    QueueResponse,
    TicketStatus,
    is_priority,
)
from contracts.service_v1.handoff import HandoffTrigger

_CONNECT_TIMEOUT_SECONDS = 5

_COLUMNS = (
    "ticket_ref, trigger, language, category, status, created_at_utc, reference_date, claimed_by"
)


_CLOSED_STATUSES = (TicketStatus.RESOLVED.value, TicketStatus.REJECTED.value)


def _build_query(filters: QueueFilters) -> tuple[str, tuple[object, ...]]:
    """The query and its parameters for ``filters``; a bare filter is never string-interpolated.

    A resolved or rejected ticket never appears: the queue is for tickets still needing an agent's
    attention, whether or not one has already been claimed (``in_review``) — excluding only the
    two closed statuses, rather than keeping just ``open``, is what makes ``QueueItem.status`` a
    field worth showing per ticket at all, since a queue that only ever held one status would have
    no need to report it.
    """
    clauses: list[str] = ["status NOT IN (%s, %s)"]
    params: list[object] = list(_CLOSED_STATUSES)
    if filters.language is not None:
        clauses.append("language = %s")
        params.append(filters.language)
    if filters.trigger is not None:
        clauses.append("trigger = %s")
        params.append(filters.trigger.value)
    where = f" WHERE {' AND '.join(clauses)}"
    # The column list and the WHERE clause are built from a closed set of module constants and
    # contract enum values, never request text, so there is nothing here a query-builder warning
    # need apply to.
    query = f"SELECT {_COLUMNS} FROM handoff_outbox{where}"  # noqa: S608
    return query, tuple(params)


class PostgresHandoffQueue:
    """A ``HandoffQueue`` backed by the ``handoff_outbox`` table."""

    def __init__(self, dsn: str, *, contact_days_priority: int, contact_days_default: int) -> None:
        self._dsn = dsn
        self._contact_days_priority = contact_days_priority
        self._contact_days_default = contact_days_default

    def _promised_contact_by(self, trigger: HandoffTrigger, reference_date: date) -> date:
        days = self._contact_days_priority if is_priority(trigger) else self._contact_days_default
        return reference_date + timedelta(days=days)

    def _row_to_item(self, row: Any, *, calendar: DomainCalendar) -> QueueItem:
        (
            ticket_ref,
            trigger,
            language,
            category,
            status,
            created_at,
            reference_date,
            claimed_by,
        ) = row
        trigger_enum = HandoffTrigger(trigger)
        return QueueItem(
            ticket_ref=ticket_ref,
            trigger=trigger_enum,
            language=language,
            category=DisputeCategory(category) if category is not None else None,
            status=TicketStatus(status),
            created_at=created_at,
            reference_date=reference_date,
            promised_contact_by=self._promised_contact_by(trigger_enum, reference_date),
            age_days=(calendar.reference_date - reference_date).days,
            priority=is_priority(trigger_enum),
            claimed_by=claimed_by,
        )

    def list_tickets(self, filters: QueueFilters, *, calendar: DomainCalendar) -> QueueResponse:
        """The queue as of ``calendar``'s reference date, priority tickets first, then oldest
        first."""
        query, params = _build_query(filters)
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(query, params)
            rows = cur.fetchall()
        items = [self._row_to_item(row, calendar=calendar) for row in rows]
        items.sort(key=lambda item: (not item.priority, -item.age_days, item.ticket_ref))
        return QueueResponse(
            reference_date=calendar.reference_date,
            reference_date_origin=ReferenceDateOrigin(calendar.origin.value),
            items=tuple(items),
        )

    def get_ticket(self, ticket_ref: str, *, calendar: DomainCalendar) -> QueueItem | None:
        """One ticket by reference, whatever its status — a selected ticket that has since left
        the open queue (resolved, rejected) must still resolve here (AC-E10-08), unlike
        ``list_tickets``, which excludes both on purpose."""
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            # The column list is a closed set of module constants, never request text, matching
            # `_build_query`'s own justification above.
            cur.execute(
                f"SELECT {_COLUMNS} FROM handoff_outbox WHERE ticket_ref = %s",  # noqa: S608
                (ticket_ref,),
            )
            row = cur.fetchone()
        return None if row is None else self._row_to_item(row, calendar=calendar)
