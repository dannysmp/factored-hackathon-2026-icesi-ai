"""
Proposed System Adapter Tests
===============================

Component: ``evals.runner.proposed_system``. Hermetic: FastAPI's in-process test client and a
fake, in-memory controller factory, matching ``tests.test_turns_api``'s own convention — no
database, no LLM call, no real seeded data. This proves ``run_case``'s own responsibilities
(session minting, turn sequencing, latency capture, response parsing); driving a real, fully
seeded customer through the real controller is the runner's own end-to-end concern, a later
increment.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

# Third-party libraries
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

# Local modules
from app.config import Settings, load_settings
from app.conversation.controller import DialogueController
from app.conversation.store import InMemoryDialogueStore
from app.conversation.understanding import TurnAccounting
from app.domain.policy.loader import load_policy
from app.main import create_app
from app.retrieval.lexical import LexicalRetriever
from app.security.sessions import Principal
from contracts.service_v1.cases import CaseRecord
from contracts.service_v1.envelope import Intent
from contracts.service_v1.nlu import NluIntent, NluResult
from contracts.service_v1.tools import (
    CreateDisputeCaseResult,
    EvaluateDisputeRequest,
    ToolFailure,
    TransactionFact,
    TransactionFilters,
    TransactionPage,
)
from evals.models import Case, CaseCategory
from evals.runner.proposed_system import run_case

LOGIN_KEY = "test-login-key-0123456789"
SIGNING_KEY = "s" * 40
_NOW = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)


@dataclass
class _FakeUnderstanding:
    """Returns the same small-talk understanding for every message, matching
    ``tests.test_turns_api``'s own fixture: no tool call is ever needed to reach a reply."""

    def understand(
        self, text: str, *, language_hint: str | None
    ) -> tuple[NluResult, TurnAccounting | None]:
        return NluResult(intent=NluIntent.SMALL_TALK, confidence=0.9, language="es"), None


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


@pytest.fixture(scope="module")
def _retriever() -> LexicalRetriever:
    return LexicalRetriever.from_corpus()


def _settings(**updates: Any) -> Settings:
    values: dict[str, Any] = {
        "session_signing_key": SecretStr(SIGNING_KEY),
        "test_identity_enabled": True,
        "test_identity_key": SecretStr(LOGIN_KEY),
        "service_version": "test-sha",
        "data_as_of_date": "2026-06-18",
    }
    return load_settings(env_file=None).model_copy(update={**values, **updates})


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

    def always_active(customer_id: str) -> str:
        return "Active"

    app = create_app(_settings(), customer_lookup=always_active, controller_factory=build)
    return TestClient(app)


def _case(**overrides: Any) -> Case:
    values: dict[str, Any] = {
        "case_id": "adapter-test-01",
        "category": CaseCategory.NORMAL,
        "lang": "es",
        "provenance": "observed",
        "seed_ref": "ops_seed:CLI-A1B2",
        "user_turns": ("Hola",),
        "expected_intent": Intent.POLICY_ANSWER,
        "expected_policy_section_id": "filing-windows",
    }
    return Case(**{**values, **overrides})


def test_run_case_mints_a_session_and_drives_every_scripted_turn(client: TestClient) -> None:
    case = _case(user_turns=("Hola", "Gracias"))

    transcript = run_case(client, case, customer_id="CUST-1", test_login_key=LOGIN_KEY)

    assert transcript.case is case
    assert len(transcript.replies) == 2
    assert len(transcript.latencies_seconds) == 2
    assert all(latency >= 0 for latency in transcript.latencies_seconds)


def test_run_case_gives_each_turn_a_distinct_deterministic_turn_id(client: TestClient) -> None:
    case = _case(user_turns=("Hola", "Gracias", "Adiós"))

    transcript = run_case(client, case, customer_id="CUST-1", test_login_key=LOGIN_KEY)

    assert [reply.turn_id for reply in transcript.replies] == [
        "adapter-test-01-t000",
        "adapter-test-01-t001",
        "adapter-test-01-t002",
    ]


def test_run_case_uses_the_same_conversation_id_across_every_turn(client: TestClient) -> None:
    case = _case(user_turns=("Hola", "Gracias"))

    transcript = run_case(client, case, customer_id="CUST-1", test_login_key=LOGIN_KEY)

    conversation_ids = {reply.conversation_id for reply in transcript.replies}
    assert len(conversation_ids) == 1
    assert transcript.session_id == transcript.replies[-1].conversation_id


def test_run_case_raises_when_the_sandbox_login_key_is_wrong(client: TestClient) -> None:
    case = _case()

    with pytest.raises(httpx.HTTPStatusError):
        run_case(client, case, customer_id="CUST-1", test_login_key="wrong-key-entirely")


def test_run_case_raises_when_the_customer_does_not_exist(_retriever: LexicalRetriever) -> None:
    def build(principal: Principal) -> DialogueController:
        raise AssertionError("the controller must never be built for a refused login")

    def never_active(customer_id: str) -> str | None:
        return None

    app = create_app(_settings(), customer_lookup=never_active, controller_factory=build)
    client = TestClient(app)
    case = _case()

    with pytest.raises(httpx.HTTPStatusError):
        run_case(client, case, customer_id="CUST-UNKNOWN", test_login_key=LOGIN_KEY)
