"""
B1 Conversation Loop Tests
============================

Component: ``evals.runner.baselines.b1``. ``build_b1_dependencies``'s and ``run_cases``'s prod
guards are hermetic. The full loop and the batch runner need a real, migrated Postgres for the
tool dispatcher's own store calls; marked ``integration``, skipped when ``DATABASE_URL`` is not
set. No test in this file reaches the real Anthropic API: a sequenced stub stands in for
``anthropic.Anthropic().messages``, matching ``tests.test_naive_agent_client``'s own convention,
extended here to script more than one call in a row (this module's own loop, unlike a single
``NaiveAgentClient.send``, makes several).
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
from evals.runner.baselines.b1 import _MAX_TOOL_ROUNDS, build_b1_dependencies, run_case, run_cases
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
    dsn: str, retriever: LexicalRetriever, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single customer turn: the model calls get_policy once, then replies in text — the
    shortest real loop, exercising message construction, tool dispatch and the final
    synthetic TurnResponse together. The clock NaiveAgentClient measures itself against is
    controlled here so the turn's recorded latency can be pinned to the exact sum of both calls'
    elapsed time, not merely asserted positive (a single hardcoded or last-call-only latency
    would produce a different, wrong number)."""
    ticks = iter([0.0, 0.1, 0.1, 0.35])
    monkeypatch.setattr(
        "evals.runner.baselines.naive_agent_client.time.monotonic", lambda: next(ticks)
    )
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
        expected_policy_section_id="filing-windows",
    )

    transcript = run_case(client, dispatcher, case, session_id=session_id, calendar=calendar)

    assert len(transcript.replies) == 1
    reply = transcript.replies[0]
    assert reply.reply == "Tiene 60 días para presentar la disputa."
    assert reply.conversation_id == session_id
    assert reply.handoff_ticket is None
    assert reply.next_expected is None
    assert len(stub.messages.calls) == 2
    assert transcript.latencies_seconds[0] == pytest.approx(0.35)  # 0.1s + 0.25s, summed exactly

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


@pytest.mark.integration
def test_run_case_keeps_next_expected_through_an_unrelated_tool_call_in_a_later_round(
    dsn: str, retriever: LexicalRetriever
) -> None:
    """An eligible evaluate_dispute call still tags Slot.CONFIRMATION even when a later round in
    the same turn calls an unrelated tool before the model finally replies in text — the
    signal is this turn's LAST evaluate_dispute call, not whatever the most recent round's own
    tool calls happened to be. create_dispute_case remains the only call that clears it."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO products (product_id, customer_id, product_type, last4, "
            "product_status) VALUES ('PRD-B1-LOOP2', 'CLI-B1-LOOP', 'Cuenta Corriente', '5678', "
            "'Active')"
        )
        cur.execute(
            "INSERT INTO transactions (transaction_id, customer_id, product_id, "
            "transaction_date, transaction_type, merchant_name, amount, currency, amount_usd, "
            "amount_usd_provenance, transaction_status) VALUES "
            "('TRX-B1-LOOP2', 'CLI-B1-LOOP', 'PRD-B1-LOOP2', '2026-06-08 09:00:00', 'Purchase', "
            "'Another Merchant', 100.00, 'USD', 100.00, 'reported', 'Approved')"
        )
    stub = _StubAnthropic(
        [
            _response(
                [
                    _tool_use_block(
                        "evaluate_dispute",
                        {"transaction_ref": "TRX-B1-LOOP2", "category": "unrecognized_charge"},
                        block_id="t1",
                    )
                ],
                stop_reason="tool_use",
            ),
            _response(
                [_tool_use_block("get_policy", {"query": "plazo"}, block_id="t2")],
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
        case_id="b1-loop-03",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="observed",
        seed_ref="ops_seed:TRX-B1-LOOP2",
        user_turns=("No reconozco un cargo en mi tarjeta.",),
        expected_intent=Intent.CONFIRM_FILING,
    )

    transcript = run_case(client, dispatcher, case, session_id=session_id, calendar=calendar)

    assert len(stub.messages.calls) == 3
    assert transcript.replies[0].next_expected is Slot.CONFIRMATION


@pytest.mark.integration
def test_run_case_does_not_leak_a_handoff_ticket_into_a_later_unrelated_turn(
    dsn: str, retriever: LexicalRetriever
) -> None:
    """A handoff ticket recorded in one turn does not carry into a later turn's synthetic reply
    when that later turn never calls handoff itself — start_turn resets the dispatcher's own
    ticket, the same way it already resets the reason-code list it also owns."""
    stub = _StubAnthropic(
        [
            _response(
                [_tool_use_block("handoff", {"trigger": "customer_request"}, block_id="t1")],
                stop_reason="tool_use",
            ),
            _response([_text_block("La transfiero con una persona.")], stop_reason="end_turn"),
            _response([_text_block("Con gusto, ¿en qué más le ayudo?")], stop_reason="end_turn"),
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
        case_id="b1-loop-04",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="observed",
        seed_ref="ops_seed:CLI-B1-LOOP",
        user_turns=("Quiero hablar con una persona.", "¿Algo más en qué me pueda ayudar?"),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="filing-windows",
    )

    transcript = run_case(client, dispatcher, case, session_id=session_id, calendar=calendar)

    assert transcript.replies[0].handoff_ticket is not None
    assert transcript.replies[1].handoff_ticket is None


@pytest.mark.integration
def test_run_case_stops_after_max_tool_rounds_with_no_text_reply(
    dsn: str, retriever: LexicalRetriever
) -> None:
    """A model that never stops calling tools is capped at _MAX_TOOL_ROUNDS: the turn ends with
    no text reply and the stub is asked for exactly that many calls, never one more — the bound
    that protects a real API budget from a pathological loop."""
    outcomes: list[object] = [
        _response(
            [_tool_use_block("get_policy", {"query": "cualquier cosa"}, block_id=f"t{i}")],
            stop_reason="tool_use",
        )
        for i in range(_MAX_TOOL_ROUNDS)
    ]
    stub = _StubAnthropic(outcomes)
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
        case_id="b1-loop-05",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="observed",
        seed_ref="ops_seed:CLI-B1-LOOP",
        user_turns=("¿Cuánto tiempo tengo para presentar una disputa?",),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="filing-windows",
    )

    transcript = run_case(client, dispatcher, case, session_id=session_id, calendar=calendar)

    assert transcript.replies[0].reply == "(no reply)"
    assert transcript.replies[0].next_expected is None
    assert len(stub.messages.calls) == _MAX_TOOL_ROUNDS


# -----------------------------------------------------------------------------
# run_cases — a batch, one shared client, a fresh dispatcher per case
# -----------------------------------------------------------------------------


def test_run_cases_refuses_when_app_env_is_prod() -> None:
    settings = _settings(app_env=AppEnvironment.PROD, database_url=SecretStr("postgresql://unused"))
    client = NaiveAgentClient(
        SecretStr("unused"),
        model=_MODEL,
        client=_StubAnthropic([]),  # type: ignore[arg-type]
    )

    with pytest.raises(ConfigError, match="not allowed when APP_ENV=prod"):
        run_cases(
            client,
            settings,
            "postgresql://unused",
            (),
            policy=load_policy(),
            retriever=LexicalRetriever.from_corpus(),
            calendar=DomainCalendar(reference_date=_TODAY, origin=DateOrigin.SETTING),
            clock=lambda: _NOW,
        )


@pytest.mark.integration
def test_run_cases_scopes_each_case_to_its_own_customers_data(
    dsn: str, retriever: LexicalRetriever
) -> None:
    """run_cases reuses the same client for every case (matching evals.runner.runner.run_cases's
    own httpx.Client reuse) while rebuilding a fresh, customer-scoped dispatcher and session per
    case. Proven with a transaction only the SECOND customer owns: if the dispatcher were wrongly
    reused across cases (the first case's customer leaking into the second), the second case's
    evaluate_dispute would find no matching transaction for that customer instead of the eligible
    decision it actually has, and correct_outcome would come back False."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customers (customer_id, first_name, last_name, country, "
            "customer_status) VALUES ('CLI-B1-LOOP-2', 'Second', 'Customer', 'México', 'Active')"
        )
        cur.execute(
            "INSERT INTO products (product_id, customer_id, product_type, last4, "
            "product_status) VALUES ('PRD-B1-CASES-1', 'CLI-B1-LOOP', 'Cuenta Corriente', "
            "'1111', 'Active')"
        )
        cur.execute(
            "INSERT INTO products (product_id, customer_id, product_type, last4, "
            "product_status) VALUES ('PRD-B1-CASES-2', 'CLI-B1-LOOP-2', 'Cuenta Corriente', "
            "'2222', 'Active')"
        )
        cur.execute(
            "INSERT INTO transactions (transaction_id, customer_id, product_id, "
            "transaction_date, transaction_type, merchant_name, amount, currency, amount_usd, "
            "amount_usd_provenance, transaction_status) VALUES "
            "('TRX-B1-CASES-1', 'CLI-B1-LOOP', 'PRD-B1-CASES-1', '2026-06-08 09:00:00', "
            "'Purchase', 'Merchant One', 100.00, 'USD', 100.00, 'reported', 'Approved')"
        )
        cur.execute(
            "INSERT INTO transactions (transaction_id, customer_id, product_id, "
            "transaction_date, transaction_type, merchant_name, amount, currency, amount_usd, "
            "amount_usd_provenance, transaction_status) VALUES "
            "('TRX-B1-CASES-2', 'CLI-B1-LOOP-2', 'PRD-B1-CASES-2', '2026-06-09 09:00:00', "
            "'Purchase', 'Merchant Two', 50.00, 'USD', 50.00, 'reported', 'Approved')"
        )
    stub = _StubAnthropic(
        [
            _response(
                [
                    _tool_use_block(
                        "evaluate_dispute",
                        {"transaction_ref": "TRX-B1-CASES-1", "category": "unrecognized_charge"},
                        block_id="t1",
                    )
                ],
                stop_reason="tool_use",
            ),
            _response([_text_block("¿Confirma la disputa?")], stop_reason="end_turn"),
            _response(
                [
                    _tool_use_block(
                        "evaluate_dispute",
                        {"transaction_ref": "TRX-B1-CASES-2", "category": "unrecognized_charge"},
                        block_id="t2",
                    )
                ],
                stop_reason="tool_use",
            ),
            _response([_text_block("¿Confirma la disputa?")], stop_reason="end_turn"),
        ]
    )
    client = NaiveAgentClient(SecretStr("unused"), model=_MODEL, client=stub)  # type: ignore[arg-type]
    settings = _settings(database_url=SecretStr(dsn))
    calendar = DomainCalendar(reference_date=_TODAY, origin=DateOrigin.SETTING)
    cases = (
        Case(
            case_id="b1-cases-01",
            category=CaseCategory.NORMAL,
            lang="es",
            provenance="observed",
            seed_ref="ops_seed:TRX-B1-CASES-1",
            user_turns=("No reconozco un cargo en mi tarjeta.",),
            expected_intent=Intent.CONFIRM_FILING,
        ),
        Case(
            case_id="b1-cases-02",
            category=CaseCategory.NORMAL,
            lang="es",
            provenance="observed",
            seed_ref="ops_seed:TRX-B1-CASES-2",
            user_turns=("No reconozco un cargo en mi tarjeta.",),
            expected_intent=Intent.CONFIRM_FILING,
        ),
    )

    results = run_cases(
        client,
        settings,
        dsn,
        cases,
        policy=load_policy(),
        retriever=retriever,
        calendar=calendar,
        clock=lambda: _NOW,
    )

    assert [r.case_id for r in results] == ["b1-cases-01", "b1-cases-02"]
    assert all(r.correct_outcome for r in results)
    assert len(stub.messages.calls) == 4
