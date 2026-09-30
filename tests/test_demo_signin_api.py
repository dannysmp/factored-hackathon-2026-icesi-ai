"""
Demo Sign-In Broker Tests
==========================

Component: ``app.api.demo_signin`` through ``app.main.create_app``. Hermetic: FastAPI's in-process
client, an injected clock, a fake customer lookup and a fake, in-memory sign-in audit sink.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.main as main_module
from app.config import Settings, load_settings
from app.domain.calendar import DomainCalendar
from app.main import AgentConsolePorts, create_app
from app.security.demo_personas import load_personas
from app.security.signin_audit import SignInAuditRecord
from contracts.service_v1.console import QueueFilters, QueueResponse, TicketDetail

START = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)
ACCESS_CODE = "demo-access-code-0123456789"
AGENT_ACCESS_CODE = "agent-access-code-9876543210"
SIGNING_KEY = "s" * 40
AGENT_SIGNING_KEY = "a" * 40
DEMO_LOGIN = "/v1/auth/demo-sessions"
AGENT_LOGIN = "/v1/auth/demo-agent-sessions"
DEMO_PERSONAS = "/v1/auth/demo-personas"

_PERSONAS = """
version: 1
customers:
  - slug: ana
    display_name: Ana
    customer_id: CUST-1
    language: es
    scenario: eligible
  - slug: joao
    display_name: João
    customer_id: CUST-2
    language: pt
    scenario: repeat_complainer
  - slug: emma
    display_name: Emma
    customer_id: CUST-3
    language: en
    scenario: eligible
  - slug: carlos
    display_name: Carlos
    customer_id: CUST-4
    language: es
    scenario: eligible
  - slug: mariana
    display_name: Mariana
    customer_id: CUST-5
    language: pt
    scenario: eligible
agents:
  - slug: agent-beatriz
    display_name: Beatriz
    agent_id: AGENT-1
    languages: [pt, es]
    specialty: null
  - slug: agent-diego
    display_name: Diego
    agent_id: AGENT-2
    languages: [es]
    specialty: fraud
"""


class Clock:
    """A clock that only moves when the test says so."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


class _RecordingAuditSink:
    """A fake ``SignInAuditSink`` that remembers every record, for asserting on the trail."""

    def __init__(self) -> None:
        self.records: list[SignInAuditRecord] = []

    def record(self, entry: SignInAuditRecord) -> None:
        self.records.append(entry)


class _FailingAuditSink:
    """A fake ``SignInAuditSink`` that always refuses to write, for the fail-closed test."""

    def record(self, entry: SignInAuditRecord) -> None:
        raise RuntimeError("audit store is down")


class _FlakyAuditSink:
    """A fake ``SignInAuditSink`` that fails its first ``fail_times`` calls, then succeeds."""

    def __init__(self, fail_times: int) -> None:
        self._fail_times = fail_times
        self.records: list[SignInAuditRecord] = []

    def record(self, entry: SignInAuditRecord) -> None:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("audit store is down")
        self.records.append(entry)


def _settings(**updates: Any) -> Settings:
    values: dict[str, Any] = {
        "session_signing_key": SecretStr(SIGNING_KEY),
        "demo_signin_enabled": True,
        "demo_signin_access_code": SecretStr(ACCESS_CODE),
        "service_version": "test-sha",
        "data_as_of_date": "2026-06-18",
    }
    return load_settings(env_file=None).model_copy(update={**values, **updates})


def _agent_settings(**updates: Any) -> Settings:
    values: dict[str, Any] = {
        "demo_agent_signin_enabled": True,
        "demo_agent_access_code": SecretStr(AGENT_ACCESS_CODE),
        "agent_session_signing_key": SecretStr(AGENT_SIGNING_KEY),
    }
    return _settings(**{**values, **updates})


def _always_active(customer_id: str) -> str | None:
    return "Active"


class _UnreachableAgentConsole:
    """An ``AgentConsolePorts``-shaped bundle whose methods are never actually called: these
    tests are about the sign-in broker, not the console's read routes, and injecting this avoids
    requiring a real ``DATABASE_URL`` just to build the routes' collaborators."""

    def list_tickets(self, filters: QueueFilters, *, calendar: DomainCalendar) -> QueueResponse:
        raise NotImplementedError

    def get_ticket_detail(
        self, ticket_ref: str, *, calendar: DomainCalendar
    ) -> TicketDetail | None:
        raise NotImplementedError

    def packet_viewed(self, **kwargs: object) -> None:
        raise NotImplementedError

    def timeline_viewed(self, **kwargs: object) -> None:
        raise NotImplementedError


def _agent_console() -> AgentConsolePorts:
    unreachable = _UnreachableAgentConsole()
    return AgentConsolePorts(
        queue=unreachable,
        ticket_detail=unreachable,
        audit=unreachable,
    )


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def audit() -> _RecordingAuditSink:
    return _RecordingAuditSink()


@pytest.fixture(autouse=True)
def _personas_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "personas.yaml"
    path.write_text(_PERSONAS, encoding="utf-8")
    monkeypatch.setattr(main_module, "load_personas", lambda: load_personas(path))


@pytest.fixture
def app(clock: Clock, audit: _RecordingAuditSink) -> FastAPI:
    return create_app(_settings(), clock=clock, customer_lookup=_always_active, signin_audit=audit)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def agent_app(clock: Clock, audit: _RecordingAuditSink) -> FastAPI:
    """Both brokers enabled together, matching the deployed configuration (ADR-18)."""
    return create_app(
        _agent_settings(),
        clock=clock,
        customer_lookup=_always_active,
        signin_audit=audit,
        agent_console=_agent_console(),
    )


@pytest.fixture
def agent_client(agent_app: FastAPI) -> TestClient:
    return TestClient(agent_app, raise_server_exceptions=False)


def _assert_problem(response: Any, status: int, code: str, *, reauth: bool) -> dict[str, Any]:
    body = dict(response.json())
    assert response.status_code == status
    assert body["code"] == code and body["status"] == status
    assert body["reauth_required"] is reauth
    return body


def test_a_known_persona_receives_a_thirty_minute_demo_session(
    client: TestClient, audit: _RecordingAuditSink
) -> None:
    response = client.post(
        DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": ACCESS_CODE}
    )

    body = response.json()
    assert response.status_code == 201
    assert body["expires_in"] == 30 * 60

    session = client.get("/v1/session", headers={"Authorization": f"Bearer {body['access_token']}"})
    info = session.json()
    assert info["customer_id"] == "CUST-1"
    assert info["audience"] == "customer"
    assert info["demo"] is True

    assert len(audit.records) == 1
    record = audit.records[0]
    assert record.outcome.value == "issued"
    assert record.persona_slug == "ana"
    assert record.resolved_customer_id == "CUST-1"
    assert record.session_id == info["session_id"]


def test_a_document_number_or_customer_identifier_is_never_accepted(client: TestClient) -> None:
    for payload in (
        {"document_number": "123"},
        {"customer_id": "CUST-1"},
        {"persona": "ana", "extra": 1},
    ):
        response = client.post(
            DEMO_LOGIN, json=payload, headers={"X-Demo-Access-Code": ACCESS_CODE}
        )
        assert response.status_code == 422
        assert "access_token" not in response.json()


@pytest.mark.parametrize("code", [None, "", "wrong", ACCESS_CODE + "x"])
def test_a_wrong_or_missing_access_code_is_refused(
    client: TestClient, audit: _RecordingAuditSink, code: str | None
) -> None:
    headers = {} if code is None else {"X-Demo-Access-Code": code}

    response = client.post(DEMO_LOGIN, json={"persona": "ana"}, headers=headers)

    _assert_problem(response, 401, "demo_signin_rejected", reauth=True)
    assert audit.records[-1].reason_code.value == "invalid_access_code"


def test_an_unknown_persona_gets_the_same_refusal_as_a_wrong_access_code(
    client: TestClient, audit: _RecordingAuditSink
) -> None:
    wrong_code = client.post(
        DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": "wrong"}
    )
    unknown_persona = client.post(
        DEMO_LOGIN, json={"persona": "nobody"}, headers={"X-Demo-Access-Code": ACCESS_CODE}
    )

    a = _assert_problem(wrong_code, 401, "demo_signin_rejected", reauth=True)
    b = _assert_problem(unknown_persona, 401, "demo_signin_rejected", reauth=True)
    assert {k: v for k, v in a.items() if k != "request_id"} == {
        k: v for k, v in b.items() if k != "request_id"
    }
    assert audit.records[-1].reason_code.value == "unknown_persona"
    assert audit.records[-1].persona_slug == "nobody"


def test_repeated_wrong_codes_are_limited_but_the_correct_code_always_succeeds(
    client: TestClient,
) -> None:
    for _ in range(40):
        client.post(DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": "wrong"})

    response = client.post(
        DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": ACCESS_CODE}
    )

    assert response.status_code == 201


def test_the_persona_slot_cap_refuses_a_second_concurrent_session(client: TestClient) -> None:
    first = client.post(
        DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": ACCESS_CODE}
    )
    assert first.status_code == 201

    second = client.post(
        DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": ACCESS_CODE}
    )

    _assert_problem(second, 429, "too_many_attempts", reauth=False)
    assert second.headers["retry-after"]


def test_a_different_persona_is_unaffected_by_another_personas_slot_cap(
    client: TestClient,
) -> None:
    client.post(DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": ACCESS_CODE})

    other = client.post(
        DEMO_LOGIN, json={"persona": "joao"}, headers={"X-Demo-Access-Code": ACCESS_CODE}
    )

    assert other.status_code == 201


def test_a_visitor_can_sign_into_every_customer_persona_in_one_sitting(
    client: TestClient,
) -> None:
    """The address cap must not refuse a real evaluation session stepping through the roster."""
    for slug in ("ana", "joao", "emma", "carlos", "mariana"):
        response = client.post(
            DEMO_LOGIN, json={"persona": slug}, headers={"X-Demo-Access-Code": ACCESS_CODE}
        )
        assert response.status_code == 201, f"{slug} was refused: {response.json()}"


def test_the_address_cap_trusts_only_the_last_forwarded_hop(
    client: TestClient, audit: _RecordingAuditSink
) -> None:
    """A caller-supplied earlier hop in the chain must not let one visitor pose as another."""
    client.post(
        DEMO_LOGIN,
        json={"persona": "ana"},
        headers={
            "X-Demo-Access-Code": ACCESS_CODE,
            "X-Forwarded-For": "198.51.100.1, 203.0.113.30",
        },
    )

    other = client.post(
        DEMO_LOGIN,
        json={"persona": "joao"},
        headers={
            "X-Demo-Access-Code": ACCESS_CODE,
            # A spoofed earlier hop claiming the first visitor's own trusted address; only the
            # last, proxy-appended entry may ever be trusted.
            "X-Forwarded-For": "203.0.113.30, 203.0.113.40",
        },
    )

    assert other.status_code == 201
    hashes = {record.client_address_hash for record in audit.records}
    assert len(hashes) == 2, "the two visitors' addresses collapsed onto the same audit key"


def test_a_filing_that_cannot_be_audited_fails_closed(clock: Clock) -> None:
    app = create_app(
        _settings(), clock=clock, customer_lookup=_always_active, signin_audit=_FailingAuditSink()
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": ACCESS_CODE}
    )

    assert response.status_code == 503
    assert "access_token" not in response.json()


def test_a_failed_audit_write_releases_the_issuance_reservations_it_held(clock: Clock) -> None:
    """The default persona cap is 1: if a failed audit write left its reservation burning, an
    immediate retry for the same persona would also be refused, even though no session was ever
    delivered the first time. It must not be — this is the fix for the gap the architect's
    conformance review found in this same round."""
    app = create_app(
        _settings(),
        clock=clock,
        customer_lookup=_always_active,
        signin_audit=_FlakyAuditSink(fail_times=1),
    )
    client = TestClient(app, raise_server_exceptions=False)

    first = client.post(
        DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": ACCESS_CODE}
    )
    second = client.post(
        DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": ACCESS_CODE}
    )

    assert first.status_code == 503
    assert second.status_code == 201


def test_the_route_does_not_exist_when_the_broker_is_disabled(clock: Clock) -> None:
    plain = TestClient(create_app(_settings(demo_signin_enabled=False), clock=clock))

    response = plain.post(
        DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": ACCESS_CODE}
    )

    _assert_problem(response, 401, "session_missing", reauth=True)


def test_the_access_code_never_appears_in_the_audit_trail(
    client: TestClient, audit: _RecordingAuditSink
) -> None:
    client.post(DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": ACCESS_CODE})
    client.post(DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": "wrong"})

    for record in audit.records:
        for value in asdict(record).values():
            assert ACCESS_CODE not in str(value)


# ---------------------------------------------------------------------------
# Agent broker (ADR-17, ADR-18) — mirrors the customer broker above, audience by audience.
# ---------------------------------------------------------------------------


def test_a_known_agent_persona_receives_a_sixty_minute_demo_session(
    agent_client: TestClient, audit: _RecordingAuditSink
) -> None:
    response = agent_client.post(
        AGENT_LOGIN,
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )

    body = response.json()
    assert response.status_code == 201
    assert body["expires_in"] == 60 * 60

    assert len(audit.records) == 1
    record = audit.records[0]
    assert record.audience.value == "agent"
    assert record.outcome.value == "issued"
    assert record.persona_slug == "agent-beatriz"
    assert record.resolved_agent_id == "AGENT-1"
    assert record.resolved_customer_id is None


def test_an_agent_document_number_or_customer_identifier_is_never_accepted(
    agent_client: TestClient,
) -> None:
    for payload in (
        {"document_number": "123"},
        {"agent_id": "AGENT-1"},
        {"persona": "agent-beatriz", "extra": 1},
    ):
        response = agent_client.post(
            AGENT_LOGIN, json=payload, headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE}
        )
        assert response.status_code == 422
        assert "access_token" not in response.json()


def test_an_agent_token_is_refused_by_the_customer_only_session_endpoint(
    agent_client: TestClient,
) -> None:
    """A valid token of the wrong audience is refused exactly like no session at all (ADR-18)."""
    issued = agent_client.post(
        AGENT_LOGIN,
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )
    token = issued.json()["access_token"]

    response = agent_client.get("/v1/session", headers={"Authorization": f"Bearer {token}"})

    _assert_problem(response, 401, "session_invalid", reauth=True)


@pytest.mark.parametrize("code", [None, "", "wrong", AGENT_ACCESS_CODE + "x"])
def test_a_wrong_or_missing_agent_access_code_is_refused(
    agent_client: TestClient, audit: _RecordingAuditSink, code: str | None
) -> None:
    headers = {} if code is None else {"X-Demo-Access-Code": code}

    response = agent_client.post(AGENT_LOGIN, json={"persona": "agent-beatriz"}, headers=headers)

    _assert_problem(response, 401, "demo_signin_rejected", reauth=True)
    assert audit.records[-1].reason_code.value == "invalid_access_code"


def test_the_customer_access_code_does_not_work_on_the_agent_broker(
    agent_client: TestClient,
) -> None:
    response = agent_client.post(
        AGENT_LOGIN, json={"persona": "agent-beatriz"}, headers={"X-Demo-Access-Code": ACCESS_CODE}
    )

    _assert_problem(response, 401, "demo_signin_rejected", reauth=True)


def test_repeated_wrong_agent_codes_are_limited_but_the_correct_code_always_succeeds(
    agent_client: TestClient,
) -> None:
    for _ in range(40):
        agent_client.post(
            AGENT_LOGIN, json={"persona": "agent-beatriz"}, headers={"X-Demo-Access-Code": "wrong"}
        )

    response = agent_client.post(
        AGENT_LOGIN,
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )

    assert response.status_code == 201


def test_the_two_brokers_access_code_limiters_are_independent(agent_client: TestClient) -> None:
    """Exhausting the customer broker's limiter must not touch the agent broker's own."""
    for _ in range(40):
        agent_client.post(
            DEMO_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": "wrong"}
        )

    response = agent_client.post(
        AGENT_LOGIN,
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )

    assert response.status_code == 201


def test_an_unknown_agent_persona_gets_the_same_refusal_as_a_wrong_access_code(
    agent_client: TestClient, audit: _RecordingAuditSink
) -> None:
    wrong_code = agent_client.post(
        AGENT_LOGIN, json={"persona": "agent-beatriz"}, headers={"X-Demo-Access-Code": "wrong"}
    )
    unknown_persona = agent_client.post(
        AGENT_LOGIN,
        json={"persona": "nobody"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )

    a = _assert_problem(wrong_code, 401, "demo_signin_rejected", reauth=True)
    b = _assert_problem(unknown_persona, 401, "demo_signin_rejected", reauth=True)
    assert {k: v for k, v in a.items() if k != "request_id"} == {
        k: v for k, v in b.items() if k != "request_id"
    }
    assert audit.records[-1].reason_code.value == "unknown_persona"


def test_a_customer_slug_is_unknown_to_the_agent_broker_and_the_reverse(
    agent_client: TestClient,
) -> None:
    as_agent = agent_client.post(
        AGENT_LOGIN, json={"persona": "ana"}, headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE}
    )
    as_customer = agent_client.post(
        DEMO_LOGIN,
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": ACCESS_CODE},
    )

    _assert_problem(as_agent, 401, "demo_signin_rejected", reauth=True)
    _assert_problem(as_customer, 401, "demo_signin_rejected", reauth=True)


def test_the_agent_persona_slot_cap_refuses_a_second_concurrent_session(
    agent_client: TestClient,
) -> None:
    first = agent_client.post(
        AGENT_LOGIN,
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )
    assert first.status_code == 201

    second = agent_client.post(
        AGENT_LOGIN,
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )

    _assert_problem(second, 429, "too_many_attempts", reauth=False)


def test_a_different_agent_persona_is_unaffected_by_another_personas_slot_cap(
    agent_client: TestClient,
) -> None:
    agent_client.post(
        AGENT_LOGIN,
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )

    other = agent_client.post(
        AGENT_LOGIN,
        json={"persona": "agent-diego"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )

    assert other.status_code == 201


def test_an_agent_signin_that_cannot_be_audited_fails_closed(clock: Clock) -> None:
    app = create_app(
        _agent_settings(),
        clock=clock,
        customer_lookup=_always_active,
        signin_audit=_FailingAuditSink(),
        agent_console=_agent_console(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        AGENT_LOGIN,
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )

    assert response.status_code == 503
    assert "access_token" not in response.json()


def test_a_failed_agent_audit_write_releases_the_issuance_reservations_it_held(
    clock: Clock,
) -> None:
    app = create_app(
        _agent_settings(),
        clock=clock,
        customer_lookup=_always_active,
        signin_audit=_FlakyAuditSink(fail_times=1),
        agent_console=_agent_console(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    first = client.post(
        AGENT_LOGIN,
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )
    second = client.post(
        AGENT_LOGIN,
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )

    assert first.status_code == 503
    assert second.status_code == 201


def test_the_agent_route_does_not_exist_when_its_broker_is_disabled(
    clock: Clock, audit: _RecordingAuditSink
) -> None:
    """The customer broker stays enabled here, exercising the branch where only one of the two
    is on; the agent route must still not exist."""
    plain = TestClient(
        create_app(_settings(), clock=clock, customer_lookup=_always_active, signin_audit=audit)
    )

    response = plain.post(
        AGENT_LOGIN,
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )

    _assert_problem(response, 401, "session_missing", reauth=True)


def test_the_agent_access_code_never_appears_in_the_audit_trail(
    agent_client: TestClient, audit: _RecordingAuditSink
) -> None:
    agent_client.post(
        AGENT_LOGIN,
        json={"persona": "agent-beatriz"},
        headers={"X-Demo-Access-Code": AGENT_ACCESS_CODE},
    )
    agent_client.post(
        AGENT_LOGIN, json={"persona": "agent-beatriz"}, headers={"X-Demo-Access-Code": "wrong"}
    )

    for record in audit.records:
        for value in asdict(record).values():
            assert AGENT_ACCESS_CODE not in str(value)


# -----------------------------------------------------------------------------
# Persona directory
# -----------------------------------------------------------------------------


def test_the_persona_directory_lists_customer_personas_when_the_customer_broker_is_on(
    client: TestClient,
) -> None:
    response = client.get(DEMO_PERSONAS)

    assert response.status_code == 200
    slugs = [persona["slug"] for persona in response.json()["personas"]]
    assert slugs == ["ana", "joao", "emma", "carlos", "mariana"]


def test_the_persona_directory_omits_agent_personas_when_only_the_customer_broker_is_on(
    client: TestClient,
) -> None:
    response = client.get(DEMO_PERSONAS)

    audiences = {persona["audience"] for persona in response.json()["personas"]}
    assert audiences == {"customer"}


def test_the_persona_directory_lists_both_audiences_when_both_brokers_are_on(
    agent_client: TestClient,
) -> None:
    response = agent_client.get(DEMO_PERSONAS)

    personas = response.json()["personas"]
    slugs_by_audience = {
        audience: sorted(p["slug"] for p in personas if p["audience"] == audience)
        for audience in ("customer", "agent")
    }
    assert slugs_by_audience == {
        "customer": ["ana", "carlos", "emma", "joao", "mariana"],
        "agent": ["agent-beatriz", "agent-diego"],
    }


def test_the_persona_directory_carries_the_display_name_and_language_fields(
    client: TestClient,
) -> None:
    response = client.get(DEMO_PERSONAS)

    ana = next(p for p in response.json()["personas"] if p["slug"] == "ana")
    assert ana == {"slug": "ana", "display_name": "Ana", "language": "es", "audience": "customer"}


def test_an_agent_persona_shows_its_first_language_as_a_single_value(
    agent_client: TestClient,
) -> None:
    """agent-beatriz speaks [pt, es]; the directory's language field is singular, one row per
    persona, so it shows the first (primary) language rather than a row per language."""
    response = agent_client.get(DEMO_PERSONAS)

    beatriz = next(p for p in response.json()["personas"] if p["slug"] == "agent-beatriz")
    assert beatriz["language"] == "pt"
    assert sum(1 for p in response.json()["personas"] if p["slug"] == "agent-beatriz") == 1


def test_the_persona_directory_is_absent_when_both_brokers_are_disabled(clock: Clock) -> None:
    plain = TestClient(create_app(_settings(demo_signin_enabled=False), clock=clock))

    response = plain.get(DEMO_PERSONAS)

    _assert_problem(response, 401, "session_missing", reauth=True)


def test_the_persona_directory_never_reveals_a_customer_or_agent_identifier(
    agent_client: TestClient,
) -> None:
    body = agent_client.get(DEMO_PERSONAS).text

    for leaked in (
        "CUST-1",
        "CUST-2",
        "CUST-3",
        "CUST-4",
        "CUST-5",
        "AGENT-1",
        "AGENT-2",
        "customer_id",
        "agent_id",
        "scenario",
        "specialty",
    ):
        assert leaked not in body


def test_the_persona_directory_needs_no_access_code(client: TestClient) -> None:
    response = client.get(DEMO_PERSONAS)

    assert response.status_code == 200


def test_the_persona_directory_is_rate_limited_per_address(client: TestClient) -> None:
    for _ in range(5):
        response = client.get(DEMO_PERSONAS)
        assert response.status_code == 200

    limited = client.get(DEMO_PERSONAS)

    assert limited.status_code == 429
    assert limited.headers["retry-after"]
