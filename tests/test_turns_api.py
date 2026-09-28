"""
Turns Endpoint Contract Tests
=============================

Component: ``app.api.turns`` and ``app.main``'s wiring of the dialogue controller to HTTP.
Hermetic: FastAPI's in-process client and a fake, in-memory controller factory — no database, no
LLM call.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

# Third-party libraries
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

# Local modules
from app.config import Settings, load_settings
from app.conversation.controller import DialogueController
from app.conversation.store import InMemoryDialogueStore
from app.domain.policy.loader import load_policy
from app.main import create_app
from app.retrieval.lexical import LexicalRetriever
from app.security.errors import ErrorCode
from app.security.sessions import Principal
from contracts.service_v1.cases import CaseRecord
from contracts.service_v1.nlu import NluIntent, NluResult
from contracts.service_v1.tools import (
    CreateDisputeCaseResult,
    EvaluateDisputeRequest,
    ToolFailure,
    TransactionFact,
    TransactionFilters,
    TransactionPage,
)

LOGIN_KEY = "test-login-key-0123456789"
SIGNING_KEY = "s" * 40
LOGIN = "/v1/auth/test-sessions"
TURNS = "/v1/turns"
_NOW = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)


@dataclass
class _FakeUnderstanding:
    """Returns the same small-talk understanding for every message: no tool call is ever needed."""

    def understand(self, text: str, *, language_hint: str | None) -> NluResult:
        return NluResult(intent=NluIntent.SMALL_TALK, confidence=0.9, language="es")


class _NoopToolPort:
    """A ``ToolPort`` never actually called by the small-talk path this test exercises."""

    def list_transactions(self, filters: TransactionFilters) -> TransactionPage | ToolFailure:
        raise NotImplementedError

    def get_transaction(self, ref: str) -> TransactionFact | ToolFailure | None:
        raise NotImplementedError

    def list_dispute_cases(self) -> tuple[CaseRecord, ...] | ToolFailure:
        raise NotImplementedError

    def get_case(self, case_number: str) -> CaseRecord | ToolFailure | None:
        raise NotImplementedError

    def evaluate_dispute(self, request: EvaluateDisputeRequest) -> Any:
        raise NotImplementedError

    def create_dispute_case(self, request: Any) -> CreateDisputeCaseResult | ToolFailure:
        raise NotImplementedError


class _NoopHandoffOutbox:
    """A ``HandoffOutbox`` never actually called by the small-talk path this test exercises."""

    def record(self, content: Any, *, session_id: str, turn_id: str, trace_id: str) -> Any:
        raise NotImplementedError


def _settings(**updates: Any) -> Settings:
    values: dict[str, Any] = {
        "session_signing_key": SecretStr(SIGNING_KEY),
        "test_identity_enabled": True,
        "test_identity_key": SecretStr(LOGIN_KEY),
        "service_version": "test-sha",
        "data_as_of_date": "2026-06-18",
    }
    return load_settings(env_file=None).model_copy(update={**values, **updates})


@pytest.fixture(scope="module")
def _retriever() -> LexicalRetriever:
    return LexicalRetriever.from_corpus()


@pytest.fixture
def client(_retriever: LexicalRetriever) -> TestClient:
    store = InMemoryDialogueStore()
    policy = load_policy()

    def build(principal: Principal) -> DialogueController:
        return DialogueController(
            _FakeUnderstanding(),
            store=store,
            tool_port=_NoopToolPort(),
            retriever=_retriever,
            policy=policy,
            outbox=_NoopHandoffOutbox(),
            domain_date=date(2026, 6, 18),
            now=lambda: _NOW,
        )

    def always_active(customer_id: str) -> str | None:
        return "Active"

    app = create_app(_settings(), customer_lookup=always_active, controller_factory=build)
    return TestClient(app)


def _login(client: TestClient, customer_id: str = "CUST-1") -> str:
    response = client.post(
        LOGIN, json={"customer_id": customer_id}, headers={"X-Test-Login-Key": LOGIN_KEY}
    )
    assert response.status_code == 201
    return str(response.json()["access_token"])


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_turns_requires_a_session(client: TestClient) -> None:
    response = client.post(TURNS, json={"turn_id": "turn-00000001", "text": "hola"})

    assert response.status_code == 401
    assert response.json()["code"] == ErrorCode.SESSION_MISSING.value


def test_turns_returns_a_reply_for_an_authenticated_session(client: TestClient) -> None:
    token = _login(client)

    response = client.post(
        TURNS,
        json={"turn_id": "turn-00000001", "text": "hola"},
        headers=_bearer(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["turn_id"] == "turn-00000001"
    assert body["lang"] == "es"
    assert body["reply"]
    assert body["reference_date_line"]
    assert body["end_session"] is False


def test_turns_rejects_a_malformed_body(client: TestClient) -> None:
    token = _login(client)

    response = client.post(TURNS, json={"turn_id": "bad"}, headers=_bearer(token))

    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.VALIDATION_ERROR.value


def test_an_oversized_body_is_refused_before_authentication(
    _retriever: LexicalRetriever,
) -> None:
    """The body-size cap runs ahead of session authentication end to end: an oversized,
    unauthenticated request is refused for its size (413), never merely for lacking a session."""
    calls: list[Principal] = []

    def build(principal: Principal) -> DialogueController:
        calls.append(principal)
        raise AssertionError("the controller must never be built for a refused, oversized body")

    app = create_app(
        _settings(), customer_lookup=lambda customer_id: "Active", controller_factory=build
    )
    client = TestClient(app)

    oversized = b"x" * (64 * 1024 + 1)
    response = client.post(TURNS, content=oversized)

    assert response.status_code == 413
    assert response.json()["code"] == ErrorCode.PAYLOAD_TOO_LARGE.value
    assert not calls


def test_a_repeated_turn_id_replays_the_same_reply(client: TestClient) -> None:
    token = _login(client)
    body = {"turn_id": "turn-00000002", "text": "hola"}

    first = client.post(TURNS, json=body, headers=_bearer(token))
    second = client.post(TURNS, json=body, headers=_bearer(token))

    assert first.json()["reply"] == second.json()["reply"]
    assert first.json()["state_version"] == second.json()["state_version"]
