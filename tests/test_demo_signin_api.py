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
from app.main import create_app
from app.security.demo_personas import load_personas
from app.security.signin_audit import SignInAuditRecord

START = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)
ACCESS_CODE = "demo-access-code-0123456789"
SIGNING_KEY = "s" * 40
DEMO_LOGIN = "/v1/auth/demo-sessions"

_PERSONAS = """
version: 1
customers:
  - slug: ana
    customer_id: CUST-1
    language: es
    scenario: eligible
  - slug: joao
    customer_id: CUST-2
    language: pt
    scenario: repeat_complainer
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


def _always_active(customer_id: str) -> str | None:
    return "Active"


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
