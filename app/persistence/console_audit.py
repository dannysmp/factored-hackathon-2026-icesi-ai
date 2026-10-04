"""
Console Audit Sink
==================

Overview
--------
The real ``app.api.agent.ConsoleAuditSink``: writes one ``AuditRecord`` (``AuditAction
.PACKET_VIEWED``/``TIMELINE_VIEWED``) each time an agent opens a ticket's packet or timeline, so a
human's access to customer data is audited like any other access.

Scope
-----
In: resolving a ticket's own ``customer_id`` and ``trace_id`` (by ``ticket_ref`` alone), building
the two ``AuditRecord``s and handing them to an ``AuditSink``.
Out: the route that calls this (``app.api.agent``), the audit table itself
(``app.persistence.audit``, reused unchanged), reading anything else about the ticket
(``app.persistence.ticket_detail`` already answers the packet and the timeline themselves).

Design Principles
-----------------
- **A dedicated, minimal reader, not a new capability on ``PostgresTicketDetail``.**
  ``app.persistence.ticket_detail`` never reads ``handoff_outbox.customer_id`` at all: "there is
  no session to scope it to" is that module's whole reason to exist. This sink's one query
  (``SELECT customer_id, trace_id ... WHERE ticket_ref = %s``) reads exactly the two scalar
  columns it needs, by primary key, independent of that module, so neither does a tested
  boundary need reopening nor does a new capability sit on a module whose contract is "there is
  nothing here to scope by customer."
- **``session_id`` is the agent's own, never the outbox's stored one.**
  ``handoff_outbox.session_id`` is the *customer's* session from the original conversation;
  ``AuditAction``'s documentation is explicit that a console read's ``session_id`` is the
  agent's. The caller (``app.api.agent``) passes it; this module never reads the outbox's own
  ``session_id`` column at all.
- **``agent_id`` is recorded, not only carried.** A session is ephemeral (the agent sign-in
  broker's revocation store is in-memory); ``agent_id`` is the durable identity every agent read
  is audited with, so it goes into ``AuditRecord`` itself, not just this call's own parameters.
- **``tool_result_hash`` hashes what was actually shown**, the same rule every other read audits
  under (``app.persistence.reads``'s ``_hash(result)``): the packet for ``PACKET_VIEWED``, the
  timeline for ``TIMELINE_VIEWED``, never a placeholder, so the audit trail can later prove what
  an agent actually saw, not just that a read happened.
- **Fails closed**, the same as ``PostgresAuditSink`` and every other audit write in this
  codebase: a ticket with no matching ``handoff_outbox`` row, or a write that cannot complete,
  raises rather than silently skipping the record.

Runtime Contract
----------------
``PostgresConsoleAuditSink(dsn, *, sink, calendar, clock).packet_viewed(...)`` /
``.timeline_viewed(...)`` -> ``None``.
"""

from __future__ import annotations

# Standard libraries
import hashlib  # Canonical hash of what was actually shown
import json  # Canonical JSON form to hash
from dataclasses import dataclass

# Third-party libraries
import psycopg  # Serving-store driver

# Local modules
from app.domain.calendar import DomainCalendar
from app.security.sessions import Clock
from contracts.service_v1.audit import AuditAction, AuditRecord, AuditSink
from contracts.service_v1.console import TimelineEntry
from contracts.service_v1.handoff import HandoffPacket

_CONNECT_TIMEOUT_SECONDS = 5

_SELECT_SQL = "SELECT customer_id, trace_id FROM handoff_outbox WHERE ticket_ref = %s"


class TicketNotFoundError(Exception):
    """No ``handoff_outbox`` row exists for the ticket a caller asked to audit.

    The route that calls this sink already confirmed the ticket exists (it just read its whole
    detail), so this is reachable only if the two reads race with a store change between them;
    it fails the write closed rather than guessing at a ``customer_id``. The exception argument
    is the ticket reference.
    """


def _hash(payload: object) -> str:
    """SHA-256 of ``payload``'s canonical JSON form, matching ``app.persistence.reads``'s ``_hash``
    exactly (an independent copy, since that one is private to its own module)."""
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _TicketIdentity:
    """The two outbox columns an audit record needs: the ticket's customer and its trace."""

    customer_id: str
    trace_id: str


class PostgresConsoleAuditSink:
    """Writes the console's own audit records, resolving each ticket's identity by itself."""

    def __init__(
        self, dsn: str, *, sink: AuditSink, calendar: DomainCalendar, clock: Clock
    ) -> None:
        """Keep the collaborators: ``sink`` receives the records, ``calendar`` supplies the domain
        date stamped on each and ``clock`` the instant it is recorded."""
        self._dsn = dsn
        self._sink = sink
        self._calendar = calendar
        self._clock = clock

    def _ticket_identity(self, ticket_ref: str) -> _TicketIdentity:
        """The ticket's customer and trace, read by primary key.

        Raises
        ------
        TicketNotFoundError
            No ``handoff_outbox`` row exists for ``ticket_ref``.
        """
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(_SELECT_SQL, (ticket_ref,))
            row = cur.fetchone()
        if row is None:
            raise TicketNotFoundError(ticket_ref)
        customer_id, trace_id = row
        return _TicketIdentity(customer_id=customer_id, trace_id=trace_id)

    def _record(
        self,
        *,
        agent_id: str,
        session_id: str,
        ticket_ref: str,
        action: AuditAction,
        result: object,
    ) -> None:
        """Resolve the ticket's identity and record one audit entry for ``action``.

        ``result`` is what the agent was shown; only its hash is stored.
        """
        identity = self._ticket_identity(ticket_ref)
        self._sink.record(
            AuditRecord(
                trace_id=identity.trace_id,
                customer_id=identity.customer_id,
                session_id=session_id,
                action=action,
                tool_result_hash=_hash(result),
                occurred_at=self._clock(),
                domain_date=self._calendar.reference_date,
                agent_id=agent_id,
            )
        )

    def packet_viewed(
        self, *, agent_id: str, session_id: str, ticket_ref: str, packet: HandoffPacket
    ) -> None:
        """Audit that ``agent_id`` viewed ``packet`` for ``ticket_ref`` in ``session_id``.

        Raises
        ------
        TicketNotFoundError
            The ticket no longer exists.
        psycopg.Error
            The identity read or the audit write did not complete.
        """
        self._record(
            agent_id=agent_id,
            session_id=session_id,
            ticket_ref=ticket_ref,
            action=AuditAction.PACKET_VIEWED,
            result=packet.model_dump(mode="json"),
        )

    def timeline_viewed(
        self,
        *,
        agent_id: str,
        session_id: str,
        ticket_ref: str,
        timeline: tuple[TimelineEntry, ...],
    ) -> None:
        """Audit that ``agent_id`` viewed ``timeline`` for ``ticket_ref`` in ``session_id``.

        Raises
        ------
        TicketNotFoundError
            The ticket no longer exists.
        psycopg.Error
            The identity read or the audit write did not complete.
        """
        self._record(
            agent_id=agent_id,
            session_id=session_id,
            ticket_ref=ticket_ref,
            action=AuditAction.TIMELINE_VIEWED,
            result=[entry.model_dump(mode="json") for entry in timeline],
        )
