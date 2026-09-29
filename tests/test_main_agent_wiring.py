"""
Agent Console Wiring Tests
==========================

Component: ``app.main.create_app`` wiring the console's own routes (``app.api.agent``) into the
running application (ADR-17, ADR-18). Hermetic: a fake ``AgentConsolePorts`` bundle (no real
Postgres), real sign-ins through the demo brokers so the tokens under test are genuine, signed
sessions, not hand-minted ones.

ADR-18's own evidence requirement: "a test enumerates every route with each token type, including
crossing in both directions." ``tests/test_session_auth_middleware.py`` already proves the
*mechanism* against synthetic routes; this file proves it against the *real* application, now that
a real agent-audience route exists to cross into.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.main as main_module
from app.config import Settings, load_settings
from app.domain.calendar import DateOrigin, DomainCalendar
from app.main import AgentConsolePorts, create_app
from app.security.demo_personas import load_personas
from app.security.signin_audit import SignInAuditRecord
from contracts.service_v1.api import ReferenceDateOrigin
from contracts.service_v1.console import (
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


class _NoOpSignInAudit:
    def record(self, entry: SignInAuditRecord) -> None:
        pass


def _agent_console() -> AgentConsolePorts:
    return AgentConsolePorts(
        queue=_FakeQueue(), ticket_detail=_FakeTicketDetail(), audit=_FakeConsoleAudit()
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
    """Crossing in the other direction (ADR-18): the console's own token never reaches the
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
