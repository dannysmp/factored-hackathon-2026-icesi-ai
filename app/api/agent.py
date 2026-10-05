"""
Agent Routes
============

Overview
--------
The routes the human-agent console needs: the queue of open tickets, one ticket's whole
detail (its packet and its conversation's timeline), and the four narrow writes — claim or
release a ticket, add a note, and set a filed case's status. All are reachable only with an agent
session.

Scope
-----
In: the six routes and ``build_agent_router``.
Out: authenticating the request (``app.security.middleware.SessionAuthMiddleware``), the
collaborators this module reads and writes through (``app.persistence.handoff_queue``,
``app.persistence.ticket_detail``, ``app.persistence.console_audit``,
``app.persistence.agent_writes``), and mounting this router and the ``"agent"`` audience prefix
into the running application (``app.main``).

Design Principles
-----------------
- **Every read of a ticket's packet or timeline is audited before it is returned**, failing
  closed: the write happens first, and its own exception — including
  the ``NotImplementedError`` of ``AuditNotYetImplemented`` — propagates instead of being
  swallowed, so this route can never actually return agent-facing data without a
  corresponding audit record, in production or in a test.
- **Every narrow write audits itself as part of the same call** (``AgentWritesPort``'s own
  contract, matching ``PostgresAgentWrites``): a route never audits separately from the
  collaborator it calls, unlike the read routes above, which own the audit call themselves because
  the read and the write-of-the-audit-record are genuinely two different actions.
- **The queue, ticket detail and writes collaborators are built once**, not per request: none of
  them reads through a customer-scoped port (there is no customer session to scope either to), so
  there is nothing here that a shared instance could leak between two agents' requests, unlike the
  customer-facing turns route's per-request controller.
- **A ticket or a case that does not exist is a plain 404**, never the customer-facing
  foreign-reference disguise, where a reference that is not the caller's answers exactly like a
  missing one: that disguise exists so a customer cannot enumerate another customer's references
  through their own session; an agent's session is already scoped to reading and writing across
  customers by design, so there is nothing to hide a real not-found behind here.
- **A terminal case status is a 409, not a 404 or a silent no-op**: the case exists and was found;
  the request is refused because of what state it is already in, the same distinction
  ``TURN_CONFLICT`` already draws for a conversation that moved on.

Runtime Contract
----------------
``GET  /v1/agent/queue?language=&trigger=``              -> 200 ``QueueResponse`` (agent session
required).
``GET  /v1/agent/tickets/{ticket_ref}``                   -> 200 ``TicketDetail``, 404 if no such
ticket (agent session required).
``POST /v1/agent/tickets/{ticket_ref}/claim``             -> 200 ``ClaimTicketResult``, 404 if no
such ticket (agent session required).
``POST /v1/agent/tickets/{ticket_ref}/release``           -> 200 ``ClaimTicketResult``, 404 if no
such ticket (agent session required).
``POST /v1/agent/tickets/{ticket_ref}/notes``             body ``AddNoteRequest`` -> 201 ``Note``,
404 if no such ticket (agent session required).
``POST /v1/agent/tickets/{ticket_ref}/status``            body ``SetCaseStatusRequest`` -> 200
``CaseStatusResult``, 404 if no such ticket or no filed case, 409 (``case_status_terminal``) if the
case is already Resolved or Rejected (agent session required).
``ConsoleAuditSink`` (protocol): ``packet_viewed``/``timeline_viewed``, one call each per ticket
read, before the response is built.
``AgentWritesPort`` (protocol): ``claim_ticket``/``release_ticket``/``add_note``/
``set_case_status``, each auditing itself.
``build_agent_router(*, queue, ticket_detail, calendar, audit, writes) -> APIRouter``.
"""

from __future__ import annotations

# Standard libraries
from typing import Protocol  # The collaborator ports this router depends on

# Third-party libraries
from fastapi import APIRouter, Request  # Routing and request access

# Local modules
from app.api.auth import agent_principal_of  # The authenticated agent principal
from app.domain.calendar import DomainCalendar
from app.persistence.agent_writes import CaseStatusTerminal  # The one state-conflict signal
from app.security.errors import ErrorCode, ProblemError
from contracts.service_v1.cases import CaseStatus
from contracts.service_v1.console import (
    AddNoteRequest,
    CaseStatusResult,
    ClaimTicketResult,
    Note,
    QueueFilters,
    QueueItem,
    QueueResponse,
    SetCaseStatusRequest,
    TicketDetail,
    TimelineEntry,
)
from contracts.service_v1.envelope import Lang
from contracts.service_v1.handoff import HandoffPacket, HandoffTrigger


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
    """Where every packet or timeline read is audited.

    ``contracts.service_v1.audit.AuditAction.PACKET_VIEWED``/``TIMELINE_VIEWED`` exist; the real
    implementation (``app.persistence.console_audit``) resolves the ticket's own ``customer_id``
    and ``trace_id`` itself, from ``ticket_ref`` alone. This protocol hands it ``session_id`` (the
    agent's own, never the customer's own session on the original conversation — that value has no
    other source once the read completes) and the exact data shown (``packet``/``timeline``), so
    the real sink can hash what was actually returned into ``AuditRecord.tool_result_hash``, the
    same "prove what was shown, not just that something was" rule every other read audits under
    (``app.persistence.reads``'s own ``_hash(result)``). ``AuditNotYetImplemented`` is the
    fail-closed alternative that always raises, so a router built with it can never actually serve
    agent data unaudited, in production or in a test — never a silent no-op.
    """

    def packet_viewed(
        self, *, agent_id: str, session_id: str, ticket_ref: str, packet: HandoffPacket
    ) -> None:
        """Record that ``agent_id``, in ``session_id``, opened ``ticket_ref``'s ``packet``."""
        ...

    def timeline_viewed(
        self,
        *,
        agent_id: str,
        session_id: str,
        ticket_ref: str,
        timeline: tuple[TimelineEntry, ...],
    ) -> None:
        """Record that ``agent_id``, in ``session_id``, opened ``ticket_ref``'s ``timeline``."""
        ...


class AgentWritesPort(Protocol):
    """Where the four narrow agent writes go;
    ``app.persistence.agent_writes.PostgresAgentWrites`` implements this today.

    Each call audits itself with the acting agent's own identity: a route here never
    issues a separate audit call the way the two read routes above do, since the write and its
    audit record are one collaborator call, not two. A ``None`` return means no such ticket (or,
    for ``set_case_status``, no filed case) — the same not-found contract ``TicketDetailPort``
    already carries. ``CaseStatusTerminal`` (``app.persistence.agent_writes``) is the one
    exception a route here needs to translate into a problem document.
    """

    def claim_ticket(self, *, agent_id: str, session_id: str, ticket_ref: str) -> QueueItem | None:
        """Claim ``ticket_ref`` for ``agent_id``, overwriting any prior claim."""
        ...

    def release_ticket(
        self, *, agent_id: str, session_id: str, ticket_ref: str
    ) -> QueueItem | None:
        """Release ``ticket_ref``'s claim, whoever held it."""
        ...

    def add_note(
        self, *, agent_id: str, session_id: str, ticket_ref: str, note_text: str
    ) -> Note | None:
        """Append a note to ``ticket_ref``, authored by ``agent_id``."""
        ...

    def set_case_status(
        self, *, agent_id: str, session_id: str, ticket_ref: str, status: CaseStatus
    ) -> CaseStatusResult | None:
        """Set ``ticket_ref``'s filed case to ``status``."""
        ...


class AuditNotYetImplemented:
    """A fail-closed ``ConsoleAuditSink`` for a composition that has no real audit sink.

    Raises ``NotImplementedError`` unconditionally: fails the ticket-detail route closed rather
    than ever serving a packet or a timeline with no audit record.
    """

    def packet_viewed(
        self, *, agent_id: str, session_id: str, ticket_ref: str, packet: HandoffPacket
    ) -> None:
        """Refuse to record a packet read, so the route cannot return the packet unaudited."""
        raise NotImplementedError(
            "the console's audit-of-agent-reads write is not wired into this application"
        )

    def timeline_viewed(
        self,
        *,
        agent_id: str,
        session_id: str,
        ticket_ref: str,
        timeline: tuple[TimelineEntry, ...],
    ) -> None:
        """Refuse to record a timeline read, so the route cannot return the timeline unaudited."""
        raise NotImplementedError(
            "the console's audit-of-agent-reads write is not wired into this application"
        )


_NOT_FOUND_TITLE = "Not found"
_NOT_FOUND_DETAIL = "No ticket exists with that reference."


def _not_found() -> ProblemError:
    return ProblemError(ErrorCode.NOT_FOUND, 404, _NOT_FOUND_TITLE, _NOT_FOUND_DETAIL)


def build_agent_router(
    *,
    queue: QueuePort,
    ticket_detail: TicketDetailPort,
    calendar: DomainCalendar,
    audit: ConsoleAuditSink,
    writes: AgentWritesPort,
) -> APIRouter:
    """Build the agent routes.

    Parameters
    ----------
    queue : QueuePort
        Answers the queue listing.
    ticket_detail : TicketDetailPort
        Answers one ticket's whole detail.
    calendar : DomainCalendar
        The domain date every age and contact-target computation reads against; built once at
        start-up, matching every other reader of it in this codebase.
    audit : ConsoleAuditSink
        Where every packet or timeline read is recorded; see the protocol's own docstring for why
        this is injected rather than called directly.
    writes : AgentWritesPort
        Where the four narrow writes go; each call audits itself.
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
            raise _not_found()
        audit.packet_viewed(
            agent_id=agent.agent_id,
            session_id=agent.session_id,
            ticket_ref=ticket_ref,
            packet=detail.packet,
        )
        audit.timeline_viewed(
            agent_id=agent.agent_id,
            session_id=agent.session_id,
            ticket_ref=ticket_ref,
            timeline=detail.timeline,
        )
        return detail

    @router.post("/v1/agent/tickets/{ticket_ref}/claim")
    def claim_ticket(ticket_ref: str, request: Request) -> ClaimTicketResult:
        """Claim ``ticket_ref`` for the acting agent, overwriting any prior claim."""
        agent = agent_principal_of(request)
        item = writes.claim_ticket(
            agent_id=agent.agent_id, session_id=agent.session_id, ticket_ref=ticket_ref
        )
        if item is None:
            raise _not_found()
        return ClaimTicketResult(item=item)

    @router.post("/v1/agent/tickets/{ticket_ref}/release")
    def release_ticket(ticket_ref: str, request: Request) -> ClaimTicketResult:
        """Release ``ticket_ref``'s claim, whoever held it."""
        agent = agent_principal_of(request)
        item = writes.release_ticket(
            agent_id=agent.agent_id, session_id=agent.session_id, ticket_ref=ticket_ref
        )
        if item is None:
            raise _not_found()
        return ClaimTicketResult(item=item)

    @router.post("/v1/agent/tickets/{ticket_ref}/notes", status_code=201)
    def add_note(ticket_ref: str, body: AddNoteRequest, request: Request) -> Note:
        """Append a note to ``ticket_ref``, authored by the acting agent."""
        agent = agent_principal_of(request)
        note = writes.add_note(
            agent_id=agent.agent_id,
            session_id=agent.session_id,
            ticket_ref=ticket_ref,
            note_text=body.note_text,
        )
        if note is None:
            raise _not_found()
        return note

    @router.post("/v1/agent/tickets/{ticket_ref}/status")
    def set_case_status(
        ticket_ref: str, body: SetCaseStatusRequest, request: Request
    ) -> CaseStatusResult:
        """Set ``ticket_ref``'s filed case to the requested status."""
        agent = agent_principal_of(request)
        try:
            result = writes.set_case_status(
                agent_id=agent.agent_id,
                session_id=agent.session_id,
                ticket_ref=ticket_ref,
                status=body.status,
            )
        except CaseStatusTerminal:
            raise ProblemError(
                ErrorCode.CASE_STATUS_TERMINAL,
                409,
                "The case is already closed",
                "A resolved or rejected case cannot change status again.",
            ) from None
        if result is None:
            raise _not_found()
        return result

    return router
