"""
Agent Routes Tests
==================

Component: ``app.api.agent``. Hermetic: a fake queue, ticket-detail and audit sink, and a
standalone FastAPI app carrying only this router, not the full ``create_app()`` — the real wiring
(``app.main``, a real ``ConsoleAuditSink``) is exercised in ``tests/test_main_agent_wiring.py``. A
tiny middleware sets ``request.state.principal`` directly, standing in for the real
``SessionAuthMiddleware``, which is exercised elsewhere.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

# Third-party libraries
import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

# Local modules
from app.api.agent import (
    AgentWritesPort,
    AuditNotYetImplemented,
    ConsoleAuditSink,
    build_agent_router,
)
from app.domain.calendar import DateOrigin, DomainCalendar
from app.persistence.agent_writes import CaseStatusTerminal
from app.security.errors import ProblemError, problem_response
from app.security.sessions import AgentPrincipal, Principal
from contracts.service_v1.api import ReferenceDateOrigin
from contracts.service_v1.cases import CaseStatus
from contracts.service_v1.console import (
    CaseStatusResult,
    Note,
    QueueFilters,
    QueueItem,
    QueueResponse,
    TicketDetail,
    TicketStatus,
)
from contracts.service_v1.handoff import CustomerLabel, Evidence, HandoffPacket, HandoffTrigger

_NOW = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)
_CALENDAR = DomainCalendar(reference_date=date(2026, 6, 18), origin=DateOrigin.SETTING)
_AGENT = AgentPrincipal(agent_id="AGT-1", session_id="sess-agent", issued_at=_NOW, expires_at=_NOW)
_CUSTOMER = Principal(
    customer_id="CLI-1",
    session_id="sess-customer",
    issued_at=_NOW,
    expires_at=_NOW,
    audience="customer",
)

_ITEM = QueueItem(
    ticket_ref="T-20260618-AAAAAAAA",
    trigger=HandoffTrigger.FRAUD_REPORT,
    language="es",
    status=TicketStatus.OPEN,
    created_at=_NOW,
    reference_date=date(2026, 6, 18),
    promised_contact_by=date(2026, 6, 19),
    age_days=0,
    priority=True,
)
_PACKET = HandoffPacket(
    ticket_ref=_ITEM.ticket_ref,
    reference_date=_ITEM.reference_date,
    created_at=_ITEM.created_at,
    language=_ITEM.language,
    needs_language_routing=False,
    trigger=_ITEM.trigger,
    customer=CustomerLabel(first_name="Ana", masked_id="****34"),
    request_summary="Reported a possible fraud.",
    evidence=Evidence(reason_codes=(), policy_version="2"),
)
_DETAIL = TicketDetail(item=_ITEM, packet=_PACKET, timeline=())


@dataclass
class _FakeQueue:
    calls: list[QueueFilters] = field(default_factory=list)

    def list_tickets(self, filters: QueueFilters, *, calendar: DomainCalendar) -> QueueResponse:
        self.calls.append(filters)
        return QueueResponse(
            reference_date=calendar.reference_date,
            reference_date_origin=ReferenceDateOrigin.SETTING,
            items=(_ITEM,),
        )


@dataclass
class _FakeTicketDetail:
    detail: TicketDetail | None = _DETAIL
    calls: list[str] = field(default_factory=list)

    def get_ticket_detail(
        self, ticket_ref: str, *, calendar: DomainCalendar
    ) -> TicketDetail | None:
        self.calls.append(ticket_ref)
        return self.detail


@dataclass
class _FakeAudit:
    packet_calls: list[tuple[str, str, str]] = field(default_factory=list)
    timeline_calls: list[tuple[str, str, str]] = field(default_factory=list)

    def packet_viewed(
        self, *, agent_id: str, session_id: str, ticket_ref: str, packet: HandoffPacket
    ) -> None:
        self.packet_calls.append((agent_id, session_id, ticket_ref))

    def timeline_viewed(
        self,
        *,
        agent_id: str,
        session_id: str,
        ticket_ref: str,
        timeline: tuple[object, ...],
    ) -> None:
        self.timeline_calls.append((agent_id, session_id, ticket_ref))


@dataclass
class _FakeAgentWrites:
    item: QueueItem | None = _ITEM
    note: Note | None = None
    case_status_result: CaseStatusResult | None = None
    raise_terminal: bool = False
    claim_calls: list[tuple[str, str, str]] = field(default_factory=list)
    release_calls: list[tuple[str, str, str]] = field(default_factory=list)
    note_calls: list[tuple[str, str, str, str]] = field(default_factory=list)
    status_calls: list[tuple[str, str, str, CaseStatus]] = field(default_factory=list)

    def claim_ticket(self, *, agent_id: str, session_id: str, ticket_ref: str) -> QueueItem | None:
        self.claim_calls.append((agent_id, session_id, ticket_ref))
        return self.item

    def release_ticket(
        self, *, agent_id: str, session_id: str, ticket_ref: str
    ) -> QueueItem | None:
        self.release_calls.append((agent_id, session_id, ticket_ref))
        return self.item

    def add_note(
        self, *, agent_id: str, session_id: str, ticket_ref: str, note_text: str
    ) -> Note | None:
        self.note_calls.append((agent_id, session_id, ticket_ref, note_text))
        if self.item is None:
            return None
        return self.note or Note(agent_id=agent_id, note_text=note_text, created_at=_NOW)

    def set_case_status(
        self, *, agent_id: str, session_id: str, ticket_ref: str, status: CaseStatus
    ) -> CaseStatusResult | None:
        self.status_calls.append((agent_id, session_id, ticket_ref, status))
        if self.raise_terminal:
            raise CaseStatusTerminal(ticket_ref)
        if self.item is None:
            return None
        return self.case_status_result or CaseStatusResult(case_number="CASE-1", status=status)


def _client(
    *,
    principal: Principal | AgentPrincipal,
    queue: _FakeQueue,
    ticket_detail: _FakeTicketDetail,
    audit: ConsoleAuditSink,
    writes: AgentWritesPort | None = None,
) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def inject_principal(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.principal = principal
        return await call_next(request)

    @app.exception_handler(ProblemError)
    async def handle_problem(request: Request, error: ProblemError) -> Response:
        return problem_response(error, "test-request")

    app.include_router(
        build_agent_router(
            queue=queue,
            ticket_detail=ticket_detail,
            calendar=_CALENDAR,
            audit=audit,
            writes=writes if writes is not None else _FakeAgentWrites(),
        )
    )
    return TestClient(app)


# -----------------------------------------------------------------------------
# The queue route
# -----------------------------------------------------------------------------


def test_the_queue_route_answers_an_agent_session() -> None:
    queue = _FakeQueue()
    client = _client(
        principal=_AGENT, queue=queue, ticket_detail=_FakeTicketDetail(), audit=_FakeAudit()
    )

    response = client.get("/v1/agent/queue")

    assert response.status_code == 200
    assert response.json()["items"][0]["ticket_ref"] == _ITEM.ticket_ref
    assert queue.calls == [QueueFilters()]


def test_the_queue_route_forwards_language_and_trigger_filters() -> None:
    queue = _FakeQueue()
    client = _client(
        principal=_AGENT, queue=queue, ticket_detail=_FakeTicketDetail(), audit=_FakeAudit()
    )

    client.get("/v1/agent/queue", params={"language": "pt", "trigger": "fraud_report"})

    assert queue.calls == [QueueFilters(language="pt", trigger=HandoffTrigger.FRAUD_REPORT)]


def test_the_queue_route_refuses_a_customer_session() -> None:
    client = _client(
        principal=_CUSTOMER,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
    )

    response = client.get("/v1/agent/queue")

    assert response.status_code == 401
    assert response.json()["code"] == "session_missing"


def test_the_queue_route_never_touches_the_audit_sink() -> None:
    """Only viewing a ticket's packet and timeline is audited; listing the queue is not itself
    an agent's read of one ticket's own material."""
    audit = _FakeAudit()
    client = _client(
        principal=_AGENT, queue=_FakeQueue(), ticket_detail=_FakeTicketDetail(), audit=audit
    )

    client.get("/v1/agent/queue")

    assert audit.packet_calls == []
    assert audit.timeline_calls == []


# -----------------------------------------------------------------------------
# The ticket-detail route
# -----------------------------------------------------------------------------


def test_the_ticket_route_answers_an_agent_session_and_audits_both_reads() -> None:
    audit = _FakeAudit()
    client = _client(
        principal=_AGENT, queue=_FakeQueue(), ticket_detail=_FakeTicketDetail(), audit=audit
    )

    response = client.get(f"/v1/agent/tickets/{_ITEM.ticket_ref}")

    assert response.status_code == 200
    assert response.json()["item"]["ticket_ref"] == _ITEM.ticket_ref
    assert audit.packet_calls == [(_AGENT.agent_id, _AGENT.session_id, _ITEM.ticket_ref)]
    assert audit.timeline_calls == [(_AGENT.agent_id, _AGENT.session_id, _ITEM.ticket_ref)]


def test_the_ticket_route_answers_404_for_an_unknown_ticket_and_never_audits() -> None:
    audit = _FakeAudit()
    client = _client(
        principal=_AGENT,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(detail=None),
        audit=audit,
    )

    response = client.get(f"/v1/agent/tickets/{_ITEM.ticket_ref}")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert audit.packet_calls == []
    assert audit.timeline_calls == []


def test_the_ticket_route_refuses_a_customer_session() -> None:
    client = _client(
        principal=_CUSTOMER,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
    )

    response = client.get(f"/v1/agent/tickets/{_ITEM.ticket_ref}")

    assert response.status_code == 401
    assert response.json()["code"] == "session_missing"


def test_a_failing_audit_write_propagates_instead_of_serving_the_ticket() -> None:
    """Fail-closed: the route must never actually return agent data whose read was not
    audited, so the audit sink's own exception is never swallowed."""

    class _FailingAudit:
        def packet_viewed(
            self, *, agent_id: str, session_id: str, ticket_ref: str, packet: HandoffPacket
        ) -> None:
            raise RuntimeError("audit store is down")

        def timeline_viewed(
            self,
            *,
            agent_id: str,
            session_id: str,
            ticket_ref: str,
            timeline: tuple[object, ...],
        ) -> None:
            raise RuntimeError("audit store is down")

    client = _client(
        principal=_AGENT,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FailingAudit(),
    )

    with pytest.raises(RuntimeError, match="audit store is down"):
        client.get(f"/v1/agent/tickets/{_ITEM.ticket_ref}")


# -----------------------------------------------------------------------------
# The claim and release routes
# -----------------------------------------------------------------------------


def test_the_claim_route_answers_an_agent_session() -> None:
    writes = _FakeAgentWrites()
    client = _client(
        principal=_AGENT,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
        writes=writes,
    )

    response = client.post(f"/v1/agent/tickets/{_ITEM.ticket_ref}/claim")

    assert response.status_code == 200
    assert response.json()["item"]["ticket_ref"] == _ITEM.ticket_ref
    assert writes.claim_calls == [(_AGENT.agent_id, _AGENT.session_id, _ITEM.ticket_ref)]


def test_the_claim_route_answers_404_for_an_unknown_ticket() -> None:
    client = _client(
        principal=_AGENT,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
        writes=_FakeAgentWrites(item=None),
    )

    response = client.post(f"/v1/agent/tickets/{_ITEM.ticket_ref}/claim")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_the_claim_route_refuses_a_customer_session() -> None:
    client = _client(
        principal=_CUSTOMER,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
    )

    response = client.post(f"/v1/agent/tickets/{_ITEM.ticket_ref}/claim")

    assert response.status_code == 401
    assert response.json()["code"] == "session_missing"


def test_the_release_route_answers_an_agent_session() -> None:
    writes = _FakeAgentWrites()
    client = _client(
        principal=_AGENT,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
        writes=writes,
    )

    response = client.post(f"/v1/agent/tickets/{_ITEM.ticket_ref}/release")

    assert response.status_code == 200
    assert response.json()["item"]["ticket_ref"] == _ITEM.ticket_ref
    assert writes.release_calls == [(_AGENT.agent_id, _AGENT.session_id, _ITEM.ticket_ref)]


def test_the_release_route_answers_404_for_an_unknown_ticket() -> None:
    client = _client(
        principal=_AGENT,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
        writes=_FakeAgentWrites(item=None),
    )

    response = client.post(f"/v1/agent/tickets/{_ITEM.ticket_ref}/release")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_the_release_route_refuses_a_customer_session() -> None:
    client = _client(
        principal=_CUSTOMER,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
    )

    response = client.post(f"/v1/agent/tickets/{_ITEM.ticket_ref}/release")

    assert response.status_code == 401
    assert response.json()["code"] == "session_missing"


# -----------------------------------------------------------------------------
# The notes route
# -----------------------------------------------------------------------------


def test_the_notes_route_answers_an_agent_session() -> None:
    writes = _FakeAgentWrites()
    client = _client(
        principal=_AGENT,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
        writes=writes,
    )

    response = client.post(
        f"/v1/agent/tickets/{_ITEM.ticket_ref}/notes", json={"note_text": "Called back."}
    )

    assert response.status_code == 201
    assert response.json()["note_text"] == "Called back."
    assert response.json()["agent_id"] == _AGENT.agent_id
    assert writes.note_calls == [
        (_AGENT.agent_id, _AGENT.session_id, _ITEM.ticket_ref, "Called back.")
    ]


def test_the_notes_route_answers_404_for_an_unknown_ticket() -> None:
    client = _client(
        principal=_AGENT,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
        writes=_FakeAgentWrites(item=None),
    )

    response = client.post(
        f"/v1/agent/tickets/{_ITEM.ticket_ref}/notes", json={"note_text": "Called back."}
    )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_the_notes_route_refuses_a_customer_session() -> None:
    client = _client(
        principal=_CUSTOMER,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
    )

    response = client.post(
        f"/v1/agent/tickets/{_ITEM.ticket_ref}/notes", json={"note_text": "Called back."}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "session_missing"


# -----------------------------------------------------------------------------
# The case-status route
# -----------------------------------------------------------------------------


def test_the_status_route_answers_an_agent_session() -> None:
    writes = _FakeAgentWrites()
    client = _client(
        principal=_AGENT,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
        writes=writes,
    )

    response = client.post(
        f"/v1/agent/tickets/{_ITEM.ticket_ref}/status", json={"status": "Resolved"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "Resolved"
    assert writes.status_calls == [
        (_AGENT.agent_id, _AGENT.session_id, _ITEM.ticket_ref, CaseStatus.RESOLVED)
    ]


def test_the_status_route_answers_404_for_an_unknown_ticket_or_case() -> None:
    client = _client(
        principal=_AGENT,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
        writes=_FakeAgentWrites(item=None),
    )

    response = client.post(
        f"/v1/agent/tickets/{_ITEM.ticket_ref}/status", json={"status": "Resolved"}
    )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_the_status_route_answers_409_for_a_terminal_case() -> None:
    client = _client(
        principal=_AGENT,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
        writes=_FakeAgentWrites(raise_terminal=True),
    )

    response = client.post(
        f"/v1/agent/tickets/{_ITEM.ticket_ref}/status", json={"status": "Rejected"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "case_status_terminal"


def test_the_status_route_refuses_a_customer_session() -> None:
    client = _client(
        principal=_CUSTOMER,
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeAudit(),
    )

    response = client.post(
        f"/v1/agent/tickets/{_ITEM.ticket_ref}/status", json={"status": "Resolved"}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "session_missing"


# -----------------------------------------------------------------------------
# The fail-closed audit sink for a composition with no real one
# -----------------------------------------------------------------------------


def test_the_unimplemented_audit_stub_always_raises_for_both_events() -> None:
    stub = AuditNotYetImplemented()

    with pytest.raises(NotImplementedError, match="not wired"):
        stub.packet_viewed(agent_id="AGT-1", session_id="sess-1", ticket_ref="T-1", packet=_PACKET)

    with pytest.raises(NotImplementedError, match="not wired"):
        stub.timeline_viewed(agent_id="AGT-1", session_id="sess-1", ticket_ref="T-1", timeline=())
