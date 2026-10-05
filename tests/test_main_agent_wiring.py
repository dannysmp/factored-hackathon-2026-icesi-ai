"""
Agent Console Wiring Tests
==========================

Component: ``app.main.create_app`` wiring the console's own routes (``app.api.agent``) into the
running application. Most tests here are hermetic: a fake ``AgentConsolePorts`` bundle (no real
Postgres), real sign-ins through the demo brokers so the tokens under test are genuine, signed
sessions, not hand-minted ones. Two tests at the end are marked ``integration``:
``_default_agent_console`` — the real, store-backed collaborators ``create_app`` builds when no
``agent_console`` is injected — is otherwise never exercised by any test in this suite, since every
other one injects a fake precisely to avoid needing Postgres.

The separation of the two audiences is only credible if a test enumerates every route with each
token type, including crossing in both directions. ``tests/test_session_auth_middleware.py``
already proves the *mechanism* against synthetic routes; this file proves it against the *real*
application, now that a real agent-audience route exists to cross into.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.main as main_module
from app.config import ConfigError, Settings, load_settings
from app.conversation.handoff import HandoffContent
from app.domain.calendar import DateOrigin, DomainCalendar
from app.domain.policy.models import ReasonCode
from app.main import AgentConsolePorts, create_app
from app.persistence.handoff_outbox import PostgresHandoffOutbox
from app.persistence.migrate import apply_migrations
from app.security.demo_personas import load_personas
from app.security.signin_audit import SignInAuditRecord
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

ACCESS_CODE = "demo-access-code-0123456789"
AGENT_ACCESS_CODE = "agent-access-code-9876543210"
SIGNING_KEY = "s" * 40
AGENT_SIGNING_KEY = "a" * 40

_PERSONAS = """
version: 1
customers:
  - slug: ana
    display_name: Ana
    customer_id: CUST-1
    language: es
    scenario: eligible
agents:
  - slug: agent-beatriz
    display_name: Beatriz
    agent_id: AGENT-1
    languages: [es]
    specialty: null
"""

_NOW = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)
_CALENDAR = DomainCalendar(reference_date=_NOW.date(), origin=DateOrigin.SETTING)

_ITEM = QueueItem(
    ticket_ref="T-20260618-AAAAAAAA",
    trigger=HandoffTrigger.FRAUD_REPORT,
    language="es",
    status=TicketStatus.OPEN,
    created_at=_NOW,
    reference_date=_NOW.date(),
    promised_contact_by=_NOW.date(),
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


class _FakeQueue:
    def list_tickets(self, filters: QueueFilters, *, calendar: DomainCalendar) -> QueueResponse:
        return QueueResponse(
            reference_date=calendar.reference_date,
            reference_date_origin=ReferenceDateOrigin.SETTING,
            items=(_ITEM,),
        )


class _FakeTicketDetail:
    def get_ticket_detail(
        self, ticket_ref: str, *, calendar: DomainCalendar
    ) -> TicketDetail | None:
        return _DETAIL if ticket_ref == _ITEM.ticket_ref else None


class _FakeConsoleAudit:
    def packet_viewed(self, **kwargs: object) -> None:
        pass

    def timeline_viewed(self, **kwargs: object) -> None:
        pass


class _FakeAgentWrites:
    def claim_ticket(self, *, agent_id: str, session_id: str, ticket_ref: str) -> QueueItem | None:
        return _ITEM if ticket_ref == _ITEM.ticket_ref else None

    def release_ticket(
        self, *, agent_id: str, session_id: str, ticket_ref: str
    ) -> QueueItem | None:
        return _ITEM if ticket_ref == _ITEM.ticket_ref else None

    def add_note(
        self, *, agent_id: str, session_id: str, ticket_ref: str, note_text: str
    ) -> Note | None:
        if ticket_ref != _ITEM.ticket_ref:
            return None
        return Note(agent_id=agent_id, note_text=note_text, created_at=_NOW)

    def set_case_status(
        self, *, agent_id: str, session_id: str, ticket_ref: str, status: CaseStatus
    ) -> CaseStatusResult | None:
        if ticket_ref != _ITEM.ticket_ref:
            return None
        return CaseStatusResult(case_number="CASE-1", status=status)


class _NoOpSignInAudit:
    def record(self, entry: SignInAuditRecord) -> None:
        pass


def _agent_console() -> AgentConsolePorts:
    return AgentConsolePorts(
        queue=_FakeQueue(),
        ticket_detail=_FakeTicketDetail(),
        audit=_FakeConsoleAudit(),
        writes=_FakeAgentWrites(),
    )


def _settings(**updates: Any) -> Settings:
    values: dict[str, Any] = {
        "session_signing_key": SecretStr(SIGNING_KEY),
        "demo_signin_enabled": True,
        "demo_signin_access_code": SecretStr(ACCESS_CODE),
        "demo_agent_signin_enabled": True,
        "demo_agent_access_code": SecretStr(AGENT_ACCESS_CODE),
        "agent_session_signing_key": SecretStr(AGENT_SIGNING_KEY),
        "service_version": "test-sha",
        "data_as_of_date": "2026-06-18",
    }
    return load_settings(env_file=None).model_copy(update={**values, **updates})


def _always_active(customer_id: str) -> str | None:
    return "Active"


@pytest.fixture(autouse=True)
def _personas_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "personas.yaml"
    path.write_text(_PERSONAS, encoding="utf-8")
    monkeypatch.setattr(main_module, "load_personas", lambda: load_personas(path))


@pytest.fixture
def app() -> FastAPI:
    return create_app(
        _settings(),
        customer_lookup=_always_active,
        signin_audit=_NoOpSignInAudit(),
        agent_console=_agent_console(),
    )


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _agent_token(client: TestClient) -> str:
    response = client.post(
        "/v1/auth/demo-agent-sessions",
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )
    assert response.status_code == 201, response.json()
    return str(response.json()["access_token"])


def _customer_token(client: TestClient) -> str:
    response = client.post(
        "/v1/auth/demo-sessions",
        json={"persona": "ana"},
        headers={"X-Demo-Access-Code": ACCESS_CODE},
    )
    assert response.status_code == 201, response.json()
    return str(response.json()["access_token"])


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_an_agent_token_reaches_the_queue_route(client: TestClient) -> None:
    token = _agent_token(client)

    response = client.get("/v1/agent/queue", headers=_bearer(token))

    assert response.status_code == 200
    assert response.json()["items"][0]["ticket_ref"] == _ITEM.ticket_ref


def test_an_agent_token_reaches_the_ticket_detail_route(client: TestClient) -> None:
    token = _agent_token(client)

    response = client.get(f"/v1/agent/tickets/{_ITEM.ticket_ref}", headers=_bearer(token))

    assert response.status_code == 200
    assert response.json()["item"]["ticket_ref"] == _ITEM.ticket_ref


def test_a_customer_token_is_refused_on_the_queue_route(client: TestClient) -> None:
    token = _customer_token(client)

    response = client.get("/v1/agent/queue", headers=_bearer(token))

    assert response.status_code == 401
    assert response.json()["code"] == "session_invalid"


def test_a_customer_token_is_refused_on_the_ticket_detail_route(client: TestClient) -> None:
    token = _customer_token(client)

    response = client.get(f"/v1/agent/tickets/{_ITEM.ticket_ref}", headers=_bearer(token))

    assert response.status_code == 401
    assert response.json()["code"] == "session_invalid"


def test_an_agent_token_is_refused_on_the_customer_turns_route(client: TestClient) -> None:
    """Crossing in the other direction: the console's own token never reaches the
    customer's own route, even though both audiences now share the app."""
    token = _agent_token(client)

    response = client.post(
        "/v1/turns", json={"turn_id": "a" * 8, "text": "hello"}, headers=_bearer(token)
    )

    assert response.status_code == 401
    assert response.json()["code"] == "session_invalid"


def test_no_session_at_all_is_refused_on_the_queue_route(client: TestClient) -> None:
    response = client.get("/v1/agent/queue")

    assert response.status_code == 401
    assert response.json()["code"] == "session_missing"


def test_an_agent_token_reaches_the_claim_route(client: TestClient) -> None:
    token = _agent_token(client)

    response = client.post(f"/v1/agent/tickets/{_ITEM.ticket_ref}/claim", headers=_bearer(token))

    assert response.status_code == 200
    assert response.json()["item"]["ticket_ref"] == _ITEM.ticket_ref


def test_an_agent_token_reaches_the_release_route(client: TestClient) -> None:
    token = _agent_token(client)

    response = client.post(f"/v1/agent/tickets/{_ITEM.ticket_ref}/release", headers=_bearer(token))

    assert response.status_code == 200
    assert response.json()["item"]["ticket_ref"] == _ITEM.ticket_ref


def test_an_agent_token_reaches_the_notes_route(client: TestClient) -> None:
    token = _agent_token(client)

    response = client.post(
        f"/v1/agent/tickets/{_ITEM.ticket_ref}/notes",
        json={"note_text": "Called the customer back."},
        headers=_bearer(token),
    )

    assert response.status_code == 201
    assert response.json()["note_text"] == "Called the customer back."


def test_an_agent_token_reaches_the_status_route(client: TestClient) -> None:
    token = _agent_token(client)

    response = client.post(
        f"/v1/agent/tickets/{_ITEM.ticket_ref}/status",
        json={"status": "Resolved"},
        headers=_bearer(token),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "Resolved"


def test_a_customer_token_is_refused_on_the_claim_route(client: TestClient) -> None:
    token = _customer_token(client)

    response = client.post(f"/v1/agent/tickets/{_ITEM.ticket_ref}/claim", headers=_bearer(token))

    assert response.status_code == 401
    assert response.json()["code"] == "session_invalid"


def test_a_customer_token_is_refused_on_the_release_route(client: TestClient) -> None:
    token = _customer_token(client)

    response = client.post(f"/v1/agent/tickets/{_ITEM.ticket_ref}/release", headers=_bearer(token))

    assert response.status_code == 401
    assert response.json()["code"] == "session_invalid"


def test_a_customer_token_is_refused_on_the_notes_route(client: TestClient) -> None:
    token = _customer_token(client)

    response = client.post(
        f"/v1/agent/tickets/{_ITEM.ticket_ref}/notes",
        json={"note_text": "Should never be written."},
        headers=_bearer(token),
    )

    assert response.status_code == 401
    assert response.json()["code"] == "session_invalid"


def test_a_customer_token_is_refused_on_the_status_route(client: TestClient) -> None:
    token = _customer_token(client)

    response = client.post(
        f"/v1/agent/tickets/{_ITEM.ticket_ref}/status",
        json={"status": "Resolved"},
        headers=_bearer(token),
    )

    assert response.status_code == 401
    assert response.json()["code"] == "session_invalid"


def test_no_session_at_all_is_refused_on_the_claim_route(client: TestClient) -> None:
    response = client.post(f"/v1/agent/tickets/{_ITEM.ticket_ref}/claim")

    assert response.status_code == 401
    assert response.json()["code"] == "session_missing"


# -----------------------------------------------------------------------------
# `_default_agent_console`: the real, store-backed collaborators, exercised nowhere else
# -----------------------------------------------------------------------------


def test_the_agent_broker_refuses_to_start_without_a_database_or_an_injected_console() -> None:
    """`_default_agent_console` needs `DATABASE_URL` to build the real collaborators; every other
    test in this suite sidesteps it by injecting a fake `agent_console` — this is the one test
    that actually reaches that code path's own failure."""
    settings = _settings().model_copy(update={"database_url": None})

    with pytest.raises(ConfigError, match="DATABASE_URL"):
        create_app(settings, customer_lookup=_always_active, signin_audit=_NoOpSignInAudit())


@pytest.mark.integration
def test_an_agent_token_reaches_the_queue_route_through_the_real_collaborators() -> None:
    """No `agent_console` is injected here: `_default_agent_console` builds the real
    `PostgresHandoffQueue`/`PostgresTicketDetail`/`PostgresConsoleAuditSink`, and this proves a
    real request actually completes end to end through them, not just through the fakes every
    other test in this file uses."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute(
            "TRUNCATE TABLE handoff_actions, handoff_open_questions, handoff_reason_codes, "
            "handoff_sources, handoff_outbox, audit_log CASCADE"
        )
        conn.commit()
    outbox = PostgresHandoffOutbox(dsn)
    packet = outbox.record(
        HandoffContent(
            reference_date=_NOW.date(),
            created_at=_NOW,
            language="es",
            trigger=HandoffTrigger.CUSTOMER_REQUEST,
            first_name="Ana",
            customer_id="CLI-1234",
            request_summary="Wants to speak with a person.",
            reason_codes=(ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,),
            policy_version="2",
        ),
        session_id="sess-customer-original",
        turn_id="turn-1",
        trace_id="trace-1",
    )

    app = create_app(
        _settings().model_copy(update={"database_url": SecretStr(dsn)}),
        customer_lookup=_always_active,
        signin_audit=_NoOpSignInAudit(),
    )
    client = TestClient(app, raise_server_exceptions=False)
    token = _agent_token(client)

    response = client.get(f"/v1/agent/tickets/{packet.ticket_ref}", headers=_bearer(token))

    assert response.status_code == 200
    assert response.json()["item"]["ticket_ref"] == packet.ticket_ref
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT customer_id FROM audit_log WHERE action = 'packet_viewed'")
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "CLI-1234"
