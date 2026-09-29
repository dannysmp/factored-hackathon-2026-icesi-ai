"""
Agent Routes
============

Overview
--------
The two routes the human-agent console reads (ADR-17): the queue of open tickets, and one
ticket's whole detail (its packet and its conversation's timeline). Both are reachable only with
an agent session (ADR-18) and are read-only by construction — neither this module nor anything it
calls exposes a single write (AC-E10-09).

Scope
-----
In: the two routes and ``build_agent_router``.
Out: authenticating the request (``app.security.middleware.SessionAuthMiddleware``), the
collaborators this module reads through (``app.persistence.handoff_queue``,
``app.persistence.ticket_detail``), and mounting this router and the ``"agent"`` audience prefix
into the running application (``app.main`` — a later change, once the audit-of-agent-reads write
this module requires is real; see ``ConsoleAuditSink``'s own docstring).

Design Principles
-----------------
- **Every read of a ticket's packet or timeline is audited before it is returned** (AC-E10-07,
  ADR-17's own fail-closed rule): the write happens first, and its own exception — including
  ``NotImplementedError`` from ``ConsoleAuditSink``'s stub implementation — propagates instead of
  being swallowed, so this route can never actually return agent-facing data without a
  corresponding audit record, in production or in a test.
- **The queue and the ticket detail collaborators are built once**, not per request: neither reads
  through a customer-scoped port (there is no customer session to scope either to), so there is
  nothing here that a shared instance could leak between two agents' requests, unlike the
  customer-facing turns route's per-request controller.
- **A ticket that does not exist is a plain 404**, never the customer-facing foreign-reference
  disguise (AC-E4-06's "answers exactly like a missing one"): that disguise exists so a customer
  cannot enumerate another customer's references through their own session; an agent's session is
  already scoped to reading across customers by design, so there is nothing to hide a real
  not-found behind here.

Runtime Contract
----------------
``GET /v1/agent/queue?language=&trigger=``            -> 200 ``QueueResponse`` (agent session
required).
``GET /v1/agent/tickets/{ticket_ref}``                 -> 200 ``TicketDetail``, 404 if no such
ticket (agent session required).
``ConsoleAuditSink`` (protocol): ``packet_viewed``/``timeline_viewed``, one call each per ticket
read, before the response is built.
``build_agent_router(*, queue, ticket_detail, calendar, audit) -> APIRouter``.
"""

from __future__ import annotations

# Standard libraries
from typing import Protocol  # The two collaborator ports this router depends on

# Third-party libraries
from fastapi import APIRouter, Request  # Routing and request access

# Local modules
from app.api.auth import agent_principal_of  # The authenticated agent principal
from app.domain.calendar import DomainCalendar
from app.security.errors import ErrorCode, ProblemError
from contracts.service_v1.console import QueueFilters, QueueResponse, TicketDetail
from contracts.service_v1.envelope import Lang
from contracts.service_v1.handoff import HandoffTrigger


class QueuePort(Protocol):
    """Where the queue is read from; ``app.persistence.handoff_queue.PostgresHandoffQueue``
    implements this today."""

    def list_tickets(self, filters: QueueFilters, *, calendar: DomainCalendar) -> QueueResponse:
        """The queue as of ``calendar``'s reference date."""
        ...


class TicketDetailPort(Protocol):
    """Where one ticket's whole detail is read from;
    ``app.persistence.ticket_detail.PostgresTicketDetail`` implements this today."""

    def get_ticket_detail(
        self, ticket_ref: str, *, calendar: DomainCalendar
    ) -> TicketDetail | None:
        """``ticket_ref``'s whole detail, or ``None`` if no such ticket exists."""
        ...


class ConsoleAuditSink(Protocol):
    """Where every packet or timeline read is audited (AC-E10-07, ADR-17).

    Blocked, as of this module, on a change request to add ``PACKET_VIEWED``/``TIMELINE_VIEWED``
    to ``contracts.service_v1.audit.AuditAction`` (owned by a different stream) — a compatible
    addition after that contract froze, the same precedented shape as
    ``TRANSACTION_PROBED``/``CASE_PROBED``/``CASE_CREATION_REPLAYED``. This protocol exists so the
    route itself never needs to know ``AuditRecord``'s own field mapping (which ``trace_id`` a
    ticket's own read correlates to is that implementation's decision, not this router's); until a
    real implementation is injected, a stub that always raises keeps this router from ever
    actually serving agent data unaudited, in production or in a test — never a silent no-op.
    """

    def packet_viewed(self, *, agent_id: str, ticket_ref: str) -> None:
        """Record that ``agent_id`` opened ``ticket_ref``'s packet."""
        ...

    def timeline_viewed(self, *, agent_id: str, ticket_ref: str) -> None:
        """Record that ``agent_id`` opened ``ticket_ref``'s timeline."""
        ...


class AuditNotYetImplemented:
    """The ``ConsoleAuditSink`` every composition root injects until the real one exists.

    Raises ``NotImplementedError`` unconditionally: this is not a workaround for the missing
    ``AuditAction`` members, it is the correct behavior in their absence, so the ticket-detail
    route fails closed rather than ever serving a packet or a timeline with no audit record.
    """

    def packet_viewed(self, *, agent_id: str, ticket_ref: str) -> None:
        raise NotImplementedError(
            "the console's audit-of-agent-reads write is not implemented yet "
            "(blocked on AuditAction.PACKET_VIEWED)"
        )

    def timeline_viewed(self, *, agent_id: str, ticket_ref: str) -> None:
        raise NotImplementedError(
            "the console's audit-of-agent-reads write is not implemented yet "
            "(blocked on AuditAction.TIMELINE_VIEWED)"
        )


def build_agent_router(
    *,
    queue: QueuePort,
    ticket_detail: TicketDetailPort,
    calendar: DomainCalendar,
    audit: ConsoleAuditSink,
) -> APIRouter:
    """Build the agent routes.

    Parameters
    ----------
    queue : QueuePort
        Answers the queue listing.
    ticket_detail : TicketDetailPort
        Answers one ticket's whole detail.
    calendar : DomainCalendar
        The domain date every age and promised-contact computation reads against; built once at
        start-up, matching every other reader of it in this codebase.
    audit : ConsoleAuditSink
        Where every packet or timeline read is recorded; see the protocol's own docstring for why
        this is injected rather than called directly.
    """
    router = APIRouter()

    @router.get("/v1/agent/queue")
    def get_queue(
        request: Request, language: Lang | None = None, trigger: HandoffTrigger | None = None
    ) -> QueueResponse:
        """The queue of open tickets, optionally filtered by language and trigger."""
        agent_principal_of(request)
        return queue.list_tickets(
            QueueFilters(language=language, trigger=trigger), calendar=calendar
        )

    @router.get("/v1/agent/tickets/{ticket_ref}")
    def get_ticket(ticket_ref: str, request: Request) -> TicketDetail:
        """One ticket's whole packet and timeline."""
        agent = agent_principal_of(request)
        detail = ticket_detail.get_ticket_detail(ticket_ref, calendar=calendar)
        if detail is None:
            raise ProblemError(
                ErrorCode.NOT_FOUND,
                404,
                "Not found",
                "No ticket exists with that reference.",
            )
        audit.packet_viewed(agent_id=agent.agent_id, ticket_ref=ticket_ref)
        audit.timeline_viewed(agent_id=agent.agent_id, ticket_ref=ticket_ref)
        return detail

    return router
