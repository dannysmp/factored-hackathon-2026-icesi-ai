"""
B1 Conversation Loop Tests
============================

Component: ``evals.runner.baselines.b1``. ``build_b1_dependencies``'s prod guard is hermetic. The
full loop needs a real, migrated Postgres for the tool dispatcher's own store calls; marked
``integration``, skipped when ``DATABASE_URL`` is not set. No test in this file reaches the real
Anthropic API: a sequenced stub stands in for ``anthropic.Anthropic().messages``, matching
``tests.test_naive_agent_client``'s own convention, extended here to script more than one call in
a row (this module's own loop, unlike a single ``NaiveAgentClient.send``, makes several).
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import anthropic
import psycopg
import pytest
from pydantic import SecretStr

from app.config import AppEnvironment, ConfigError, Settings, load_settings
from app.domain.calendar import DateOrigin, DomainCalendar
from app.domain.policy.loader import load_policy
from app.persistence.migrate import apply_migrations
from app.retrieval.lexical import LexicalRetriever
from contracts.service_v1.envelope import Intent, Slot
from evals.models import Case, CaseCategory
from evals.runner.baselines.b1 import build_b1_dependencies, run_case
from evals.runner.baselines.naive_agent_client import NaiveAgentClient
from evals.scoring import score_case

_NOW = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)
_TODAY = date(2026, 6, 18)
_MODEL = "claude-haiku-4-5-20251001"


def _settings(**updates: Any) -> Settings:
    values: dict[str, Any] = {
        "session_signing_key": SecretStr("s" * 40),
        "anthropic_api_key": SecretStr("unused-would-fail-if-ever-called"),
        "service_version": "test-sha",
        "data_as_of_date": "2026-06-18",
    }
    return load_settings(env_file=None).model_copy(update={**values, **updates})


def test_build_b1_dependencies_refuses_when_app_env_is_prod() -> None:
    settings = _settings(app_env=AppEnvironment.PROD, database_url=SecretStr("postgresql://unused"))

    with pytest.raises(ConfigError, match="not allowed when APP_ENV=prod"):
        build_b1_dependencies(
            settings,
            policy=load_policy(),
            retriever=LexicalRetriever.from_corpus(),
            calendar=DomainCalendar(reference_date=_TODAY, origin=DateOrigin.SETTING),
            clock=lambda: _NOW,
            customer_id="CLI-UNUSED",
            lang="es",
            model=_MODEL,
        )


# -----------------------------------------------------------------------------
# The loop — needs the real store; the model is always a scripted stub
# -----------------------------------------------------------------------------


class _StubMessages:
    """Returns one scripted outcome per call, in order; raises if asked for more than scripted."""

    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._outcomes:
            raise AssertionError("the stub was asked for more calls than were scripted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _StubAnthropic:
    def __init__(self, outcomes: list[object]) -> None:
        self.messages = _StubMessages(outcomes)


def _text_block(text: str) -> anthropic.types.TextBlock:
    return anthropic.types.TextBlock(citations=None, text=text, type="text")


def _tool_use_block(name: str, arguments: dict[str, object], *, block_id: str) -> Any:
    return anthropic.types.ToolUseBlock(id=block_id, input=arguments, name=name, type="tool_use")


def _response(content: list[object], *, stop_reason: str) -> SimpleNamespace:
    return SimpleNamespace(
        model=_MODEL,
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


@pytest.fixture(scope="module")
def retriever() -> LexicalRetriever:
    return LexicalRetriever.from_corpus()


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(value)
    with psycopg.connect(value) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute(
            "TRUNCATE TABLE cases, transactions, products, customers, audit_log, "
            "handoff_outbox CASCADE"
        )
        conn.commit()
    with psycopg.connect(value, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customers (customer_id, first_name, last_name, country, "
            "customer_status) VALUES ('CLI-B1-LOOP', 'First', 'Last', 'México', 'Active')"
        )
    return value


@pytest.mark.integration
def test_run_case_answers_a_policy_question_via_one_tool_round(
    dsn: str, retriever: LexicalRetriever
) -> None:
    """A single customer turn: the model calls get_policy once, then replies in text — the
    shortest real loop, exercising message construction, tool dispatch and the final
    synthetic TurnResponse together."""
    stub = _StubAnthropic(
        [
            _response(
                [_tool_use_block("get_policy", {"query": "cuánto tiempo tengo"}, block_id="t1")],
                stop_reason="tool_use",
            ),
            _response(
                [_text_block("Tiene 60 días para presentar la disputa.")], stop_reason="end_turn"
            ),
        ]
    )
    settings = _settings(database_url=SecretStr(dsn))
    calendar = DomainCalendar(reference_date=_TODAY, origin=DateOrigin.SETTING)
    _real_client, dispatcher, session_id = build_b1_dependencies(
        settings,
        policy=load_policy(),
        retriever=retriever,
        calendar=calendar,
        clock=lambda: _NOW,
        customer_id="CLI-B1-LOOP",
        lang="es",
        model=_MODEL,
    )
    client = NaiveAgentClient(SecretStr("unused"), model=_MODEL, client=stub)  # type: ignore[arg-type]
    case = Case(
        case_id="b1-loop-01",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="observed",
        seed_ref="ops_seed:CLI-B1-LOOP",
        user_turns=("¿Cuánto tiempo tengo para presentar una disputa?",),
        expected_intent=Intent.POLICY_ANSWER,
    )

    transcript = run_case(client, dispatcher, case, session_id=session_id, calendar=calendar)

    assert len(transcript.replies) == 1
    reply = transcript.replies[0]
    assert reply.reply == "Tiene 60 días para presentar la disputa."
    assert reply.conversation_id == session_id
    assert reply.handoff_ticket is None
    assert reply.next_expected is None
    assert len(stub.messages.calls) == 2

    result = score_case(dsn, transcript)
    assert result.correct_outcome is True


@pytest.mark.integration
def test_run_case_tags_next_expected_when_a_turn_reaches_an_eligible_confirmable_state(
    dsn: str, retriever: LexicalRetriever
) -> None:
    """After an eligible evaluate_dispute call with no create_dispute_case following it in the
    same turn, the synthetic reply is tagged Slot.CONFIRMATION, the same structural signal
    evals.scoring reads from a real CONFIRM_FILING reply."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO products (product_id, customer_id, product_type, last4, "
            "product_status) VALUES ('PRD-B1-LOOP', 'CLI-B1-LOOP', 'Cuenta Corriente', '1234', "
            "'Active')"
        )
        cur.execute(
            "INSERT INTO transactions (transaction_id, customer_id, product_id, "
            "transaction_date, transaction_type, merchant_name, amount, currency, amount_usd, "
            "amount_usd_provenance, transaction_status) VALUES "
            "('TRX-B1-LOOP', 'CLI-B1-LOOP', 'PRD-B1-LOOP', '2026-06-08 09:00:00', 'Purchase', "
            "'A Merchant', 100.00, 'USD', 100.00, 'reported', 'Approved')"
        )
    stub = _StubAnthropic(
        [
            _response(
                [
                    _tool_use_block(
                        "evaluate_dispute",
                        {"transaction_ref": "TRX-B1-LOOP", "category": "unrecognized_charge"},
                        block_id="t1",
                    )
                ],
                stop_reason="tool_use",
            ),
            _response(
                [_text_block("¿Confirma que desea presentar la disputa?")], stop_reason="end_turn"
            ),
        ]
    )
    settings = _settings(database_url=SecretStr(dsn))
    calendar = DomainCalendar(reference_date=_TODAY, origin=DateOrigin.SETTING)
    _real_client, dispatcher, session_id = build_b1_dependencies(
        settings,
        policy=load_policy(),
        retriever=retriever,
        calendar=calendar,
        clock=lambda: _NOW,
        customer_id="CLI-B1-LOOP",
        lang="es",
        model=_MODEL,
    )
    client = NaiveAgentClient(SecretStr("unused"), model=_MODEL, client=stub)  # type: ignore[arg-type]
    case = Case(
        case_id="b1-loop-02",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="observed",
        seed_ref="ops_seed:TRX-B1-LOOP",
        user_turns=("No reconozco un cargo en mi tarjeta.",),
        expected_intent=Intent.CONFIRM_FILING,
    )

    transcript = run_case(client, dispatcher, case, session_id=session_id, calendar=calendar)

    assert transcript.replies[0].next_expected is Slot.CONFIRMATION
