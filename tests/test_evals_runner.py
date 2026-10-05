"""
Case Runner Tests
==================

Component: ``evals.runner.runner``. The orchestration logic is proven hermetically, against fakes
of the three already-tested pieces it sequences. The real end-to-end path — resolve, drive and
score a case against a real, migrated, freshly seeded Postgres and the real running application,
with no controller or customer-lookup override at all — needs a real store; marked
``integration``, skipped when ``DATABASE_URL`` is not set, matching this project's own convention.
"""

from __future__ import annotations

# Standard libraries
import logging
import os
from dataclasses import replace
from typing import Any, cast

# Third-party libraries
import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

# Local modules
from app.config import LlmProvider, Settings, load_settings
from app.main import create_app
from app.persistence.migrate import apply_migrations
from app.retrieval.corpus_index import CorpusIndexError
from contracts.service_v1.api import TurnResponse
from contracts.service_v1.envelope import Intent
from contracts.service_v1.tools import Tool
from evals.cost import TurnCostLedger
from evals.injector import FailureSchedule
from evals.metrics import CaseResult
from evals.models import Case, CaseCategory, InjectedToolFailure
from evals.runner.runner import run_cases
from evals.scoring import RunTranscript

LOGIN_KEY = "test-login-key-0123456789"
SIGNING_KEY = "s" * 40


def _turn_record(session_id: str, cost: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="app.conversation.controller",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=(
            f"turn_completed session_id={session_id} case_number=None model=m prompt_version=p "
            f"input_tokens=1 output_tokens=1 latency_ms=1.0 cost_usd={cost}"
        ),
        args=(),
        exc_info=None,
    )


def _case(**overrides: Any) -> Case:
    values: dict[str, Any] = {
        "case_id": "runner-test-01",
        "category": CaseCategory.NORMAL,
        "lang": "es",
        "provenance": "observed",
        "seed_ref": "ops_seed:CLI-RUNNER-A",
        "user_turns": ("¿Cuánto tiempo tengo para presentar una disputa?",),
        "expected_intent": Intent.POLICY_ANSWER,
        "expected_policy_section_id": "filing-windows",
    }
    return Case(**{**values, **overrides})


# -----------------------------------------------------------------------------
# Orchestration — hermetic, against fakes of the three already-tested pieces
# -----------------------------------------------------------------------------


def test_run_cases_resolves_drives_and_scores_every_case_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    cases = (_case(case_id="c1"), _case(case_id="c2"))

    def fake_resolve(dsn: str, seed_ref: str) -> str:
        calls.append(f"resolve:{seed_ref}")
        return f"CUST-FOR-{seed_ref}"

    def fake_run_case(client: object, case: Case, *, customer_id: str, test_login_key: str) -> str:
        calls.append(f"run:{case.case_id}:{customer_id}")
        return f"transcript-for-{case.case_id}"

    def fake_score(dsn: str, transcript: str) -> CaseResult:
        calls.append(f"score:{transcript}")
        return CaseResult(
            case_id=transcript,
            is_adversarial=False,
            expected_escalation=False,
            observed_escalation=False,
            automation_attempted=True,
            correct_outcome=True,
        )

    monkeypatch.setattr("evals.runner.runner.resolve_customer_id", fake_resolve)
    monkeypatch.setattr("evals.runner.runner.run_case", fake_run_case)
    monkeypatch.setattr("evals.runner.runner.score_case", fake_score)

    results = run_cases(cast(httpx.Client, object()), "unused-dsn", cases, test_login_key=LOGIN_KEY)

    assert calls == [
        "resolve:ops_seed:CLI-RUNNER-A",
        "run:c1:CUST-FOR-ops_seed:CLI-RUNNER-A",
        "score:transcript-for-c1",
        "resolve:ops_seed:CLI-RUNNER-A",
        "run:c2:CUST-FOR-ops_seed:CLI-RUNNER-A",
        "score:transcript-for-c2",
    ]
    assert [r.case_id for r in results] == ["transcript-for-c1", "transcript-for-c2"]


def test_run_cases_returns_an_empty_tuple_for_no_cases() -> None:
    assert run_cases(cast(httpx.Client, object()), "unused-dsn", (), test_login_key=LOGIN_KEY) == ()


def test_a_case_that_fails_to_score_is_recorded_as_a_named_error_and_the_batch_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the harness's own failure handling: one bad case must not silence
    the rest of a 135-case batch."""
    cases = (_case(case_id="fails"), _case(case_id="c2"))

    def fake_resolve(dsn: str, seed_ref: str) -> str:
        return "CUST-A"

    def fake_run_case(client: object, case: Case, *, customer_id: str, test_login_key: str) -> str:
        return f"transcript-for-{case.case_id}"

    def fake_score(dsn: str, transcript: str) -> CaseResult:
        if transcript == "transcript-for-fails":
            raise ValueError("names no transaction in the store")
        return CaseResult(
            case_id=transcript,
            is_adversarial=False,
            expected_escalation=False,
            observed_escalation=False,
            automation_attempted=True,
            correct_outcome=True,
        )

    monkeypatch.setattr("evals.runner.runner.resolve_customer_id", fake_resolve)
    monkeypatch.setattr("evals.runner.runner.run_case", fake_run_case)
    monkeypatch.setattr("evals.runner.runner.score_case", fake_score)

    results = run_cases(cast(httpx.Client, object()), "unused-dsn", cases, test_login_key=LOGIN_KEY)

    assert len(results) == 2
    failed, succeeded = results
    assert failed.case_id == "fails"
    assert failed.error is not None
    assert "names no transaction in the store" in failed.error
    assert failed.correct_outcome is False
    assert failed.automation_attempted is False
    assert succeeded.case_id == "transcript-for-c2"
    assert succeeded.error is None
    assert succeeded.correct_outcome is True


def test_a_case_that_fails_to_resolve_is_also_recorded_and_does_not_stop_later_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinct failure point from scoring: resolution itself can raise the same ValueError."""
    cases = (_case(case_id="c1", seed_ref="ops_seed:TRX-MISSING"), _case(case_id="c2"))

    def fake_resolve(dsn: str, seed_ref: str) -> str:
        if seed_ref == "ops_seed:TRX-MISSING":
            raise ValueError(f"seed_ref {seed_ref!r} names no transaction in the store")
        return "CUST-A"

    def fake_run_case(client: object, case: Case, *, customer_id: str, test_login_key: str) -> str:
        return f"transcript-for-{case.case_id}"

    def fake_score(dsn: str, transcript: str) -> CaseResult:
        return CaseResult(
            case_id=transcript,
            is_adversarial=False,
            expected_escalation=False,
            observed_escalation=False,
            automation_attempted=True,
            correct_outcome=True,
        )

    monkeypatch.setattr("evals.runner.runner.resolve_customer_id", fake_resolve)
    monkeypatch.setattr("evals.runner.runner.run_case", fake_run_case)
    monkeypatch.setattr("evals.runner.runner.score_case", fake_score)

    results = run_cases(cast(httpx.Client, object()), "unused-dsn", cases, test_login_key=LOGIN_KEY)

    assert len(results) == 2
    assert results[0].case_id == "c1"
    assert results[0].error is not None
    assert results[1].case_id == "transcript-for-c2"
    assert results[1].error is None


def test_an_unanticipated_exception_still_stops_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only this module's own documented failure classes are swallowed; anything else is a real
    bug that must not be hidden behind a misleading 'case failed' record."""
    cases = (_case(case_id="c1"), _case(case_id="c2"))

    def fake_resolve(dsn: str, seed_ref: str) -> str:
        return "CUST-A"

    def fake_run_case(client: object, case: Case, *, customer_id: str, test_login_key: str) -> str:
        raise RuntimeError("unexpected, not one of the anticipated failure classes")

    monkeypatch.setattr("evals.runner.runner.resolve_customer_id", fake_resolve)
    monkeypatch.setattr("evals.runner.runner.run_case", fake_run_case)

    with pytest.raises(RuntimeError, match="unexpected"):
        run_cases(cast(httpx.Client, object()), "unused-dsn", cases, test_login_key=LOGIN_KEY)


def test_capture_transcripts_defaults_off_and_never_calls_the_capture_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every caller before ``capture_transcripts`` existed must see zero behavior change."""
    calls: list[str] = []

    def fake_attach(dsn: str, transcript: object, result: CaseResult) -> CaseResult:
        calls.append("attach")
        return result

    def fake_score(dsn: str, transcript: str) -> CaseResult:
        return CaseResult(
            case_id=transcript,
            is_adversarial=False,
            expected_escalation=False,
            observed_escalation=False,
            automation_attempted=True,
            correct_outcome=True,
        )

    monkeypatch.setattr("evals.runner.runner.resolve_customer_id", lambda dsn, ref: "CUST-A")
    monkeypatch.setattr(
        "evals.runner.runner.run_case", lambda client, case, *, customer_id, test_login_key: "t"
    )
    monkeypatch.setattr("evals.runner.runner.score_case", fake_score)
    monkeypatch.setattr("evals.runner.runner.attach_masked_transcript", fake_attach)

    run_cases(cast(httpx.Client, object()), "unused-dsn", (_case(),), test_login_key=LOGIN_KEY)

    assert calls == []


def _stub_transcript(case: Case, session_id: str) -> RunTranscript:
    return RunTranscript(
        case=case,
        session_id=session_id,
        replies=(
            TurnResponse(
                turn_id="turn-00000001",
                conversation_id=session_id,
                state_version=1,
                lang="es",
                reply="Hola",
                reference_date_line="Fecha de referencia de los datos: 18 de junio de 2026",
            ),
        ),
        latencies_seconds=(0.1,),
    )


def _stub_score(dsn: str, transcript: RunTranscript) -> CaseResult:
    return CaseResult(
        case_id=transcript.case.case_id,
        is_adversarial=False,
        expected_escalation=False,
        observed_escalation=False,
        automation_attempted=True,
        correct_outcome=True,
        cost_usd=transcript.cost_usd,
    )


def test_a_cost_ledger_attaches_each_cases_own_session_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = {"runner-test-01": "S-1", "runner-test-02": "S-2", "runner-test-03": "S-unseen"}
    monkeypatch.setattr("evals.runner.runner.resolve_customer_id", lambda dsn, ref: "CUST-A")
    monkeypatch.setattr(
        "evals.runner.runner.run_case",
        lambda client, case, *, customer_id, test_login_key: _stub_transcript(
            case, sessions[case.case_id]
        ),
    )
    monkeypatch.setattr("evals.runner.runner.score_case", _stub_score)
    ledger = TurnCostLedger()
    ledger.emit(_turn_record("S-1", "0.5"))
    ledger.emit(_turn_record("S-2", "0.25"))

    results = run_cases(
        cast(httpx.Client, object()),
        "unused-dsn",
        tuple(_case(case_id=case_id) for case_id in sessions),
        test_login_key=LOGIN_KEY,
        cost_ledger=ledger,
    )

    assert [r.cost_usd for r in results] == [0.5, 0.25, None]


def test_the_failure_schedule_holds_each_cases_own_failure_while_it_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = InjectedToolFailure(tool=Tool.LIST_TRANSACTIONS, cause="timeout")
    schedule = FailureSchedule()
    seen: dict[str, InjectedToolFailure | None] = {}

    def recording_run_case(
        client: object, case: Case, *, customer_id: str, test_login_key: str
    ) -> RunTranscript:
        seen[case.case_id] = schedule.failure
        return _stub_transcript(case, "S-1")

    monkeypatch.setattr("evals.runner.runner.resolve_customer_id", lambda dsn, ref: "CUST-A")
    monkeypatch.setattr("evals.runner.runner.run_case", recording_run_case)
    monkeypatch.setattr("evals.runner.runner.score_case", _stub_score)

    run_cases(
        cast(httpx.Client, object()),
        "unused-dsn",
        (
            _case(case_id="healthy"),
            _case(case_id="failing", injected_failure=failure),
        ),
        test_login_key=LOGIN_KEY,
        failure_schedule=schedule,
    )

    assert seen == {"healthy": None, "failing": failure}
    assert schedule.failure is None


def test_without_a_cost_ledger_no_cost_is_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("evals.runner.runner.resolve_customer_id", lambda dsn, ref: "CUST-A")
    monkeypatch.setattr(
        "evals.runner.runner.run_case",
        lambda client, case, *, customer_id, test_login_key: _stub_transcript(case, "S-1"),
    )
    monkeypatch.setattr("evals.runner.runner.score_case", _stub_score)

    results = run_cases(
        cast(httpx.Client, object()), "unused-dsn", (_case(),), test_login_key=LOGIN_KEY
    )

    assert results[0].cost_usd is None


def test_capture_transcripts_true_attaches_the_captured_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_score(dsn: str, transcript: str) -> CaseResult:
        return CaseResult(
            case_id="c1",
            is_adversarial=False,
            expected_escalation=False,
            observed_escalation=False,
            automation_attempted=True,
            correct_outcome=True,
        )

    def fake_attach(dsn: str, transcript: object, result: CaseResult) -> CaseResult:
        return replace(result, reply_text="captured reply", facts_and_sources="captured facts")

    monkeypatch.setattr("evals.runner.runner.resolve_customer_id", lambda dsn, ref: "CUST-A")
    monkeypatch.setattr(
        "evals.runner.runner.run_case", lambda client, case, *, customer_id, test_login_key: "t"
    )
    monkeypatch.setattr("evals.runner.runner.score_case", fake_score)
    monkeypatch.setattr("evals.runner.runner.attach_masked_transcript", fake_attach)

    results = run_cases(
        cast(httpx.Client, object()),
        "unused-dsn",
        (_case(case_id="c1"),),
        test_login_key=LOGIN_KEY,
        capture_transcripts=True,
    )

    assert results[0].reply_text == "captured reply"
    assert results[0].facts_and_sources == "captured facts"


def test_a_capture_failure_leaves_the_real_verdict_standing_with_the_fields_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A capture failure (a declared policy section that does not resolve) must not demote an
    otherwise-valid ``CaseResult`` to an error result — only its two extra fields stay unset."""

    def fake_score(dsn: str, transcript: str) -> CaseResult:
        return CaseResult(
            case_id="c1",
            is_adversarial=False,
            expected_escalation=False,
            observed_escalation=False,
            automation_attempted=True,
            correct_outcome=True,
        )

    def fake_attach(dsn: str, transcript: object, result: CaseResult) -> CaseResult:
        raise KeyError("section 'nope' does not resolve in the es corpus")

    monkeypatch.setattr("evals.runner.runner.resolve_customer_id", lambda dsn, ref: "CUST-A")
    monkeypatch.setattr(
        "evals.runner.runner.run_case", lambda client, case, *, customer_id, test_login_key: "t"
    )
    monkeypatch.setattr("evals.runner.runner.score_case", fake_score)
    monkeypatch.setattr("evals.runner.runner.attach_masked_transcript", fake_attach)

    results = run_cases(
        cast(httpx.Client, object()),
        "unused-dsn",
        (_case(case_id="c1"),),
        test_login_key=LOGIN_KEY,
        capture_transcripts=True,
    )

    assert len(results) == 1
    assert results[0].error is None
    assert results[0].correct_outcome is True
    assert results[0].reply_text is None
    assert results[0].facts_and_sources is None


def test_a_capture_corpus_index_error_is_also_caught_not_only_key_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_score(dsn: str, transcript: str) -> CaseResult:
        return CaseResult(
            case_id="c1",
            is_adversarial=False,
            expected_escalation=False,
            observed_escalation=False,
            automation_attempted=True,
            correct_outcome=True,
        )

    def fake_attach(dsn: str, transcript: object, result: CaseResult) -> CaseResult:
        raise CorpusIndexError("the es corpus file could not be read")

    monkeypatch.setattr("evals.runner.runner.resolve_customer_id", lambda dsn, ref: "CUST-A")
    monkeypatch.setattr(
        "evals.runner.runner.run_case", lambda client, case, *, customer_id, test_login_key: "t"
    )
    monkeypatch.setattr("evals.runner.runner.score_case", fake_score)
    monkeypatch.setattr("evals.runner.runner.attach_masked_transcript", fake_attach)

    results = run_cases(
        cast(httpx.Client, object()),
        "unused-dsn",
        (_case(case_id="c1"),),
        test_login_key=LOGIN_KEY,
        capture_transcripts=True,
    )

    assert results[0].error is None
    assert results[0].reply_text is None


def test_an_unanticipated_exception_mid_batch_never_reaches_the_cases_behind_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real bug on a middle case propagates immediately; the cases still queued behind it are
    never even attempted — the one exception this module's own catch list does not swallow gets
    no chance to hide a case it silently skipped."""
    calls: list[str] = []
    cases = (_case(case_id="c1"), _case(case_id="c2"), _case(case_id="c3"))

    def fake_resolve(dsn: str, seed_ref: str) -> str:
        calls.append(f"resolve:{seed_ref}")
        return "CUST-A"

    def fake_run_case(client: object, case: Case, *, customer_id: str, test_login_key: str) -> str:
        calls.append(f"run:{case.case_id}")
        if case.case_id == "c2":
            raise RuntimeError("unexpected, not one of the anticipated failure classes")
        return f"transcript-for-{case.case_id}"

    def fake_score(dsn: str, transcript: str) -> CaseResult:
        calls.append(f"score:{transcript}")
        return CaseResult(
            case_id=transcript,
            is_adversarial=False,
            expected_escalation=False,
            observed_escalation=False,
            automation_attempted=True,
            correct_outcome=True,
        )

    monkeypatch.setattr("evals.runner.runner.resolve_customer_id", fake_resolve)
    monkeypatch.setattr("evals.runner.runner.run_case", fake_run_case)
    monkeypatch.setattr("evals.runner.runner.score_case", fake_score)

    with pytest.raises(RuntimeError, match="unexpected"):
        run_cases(cast(httpx.Client, object()), "unused-dsn", cases, test_login_key=LOGIN_KEY)

    # c1 ran to completion (resolved, run, scored); c2 blew up mid-run; c3 was never attempted.
    assert calls == [
        "resolve:ops_seed:CLI-RUNNER-A",
        "run:c1",
        "score:transcript-for-c1",
        "resolve:ops_seed:CLI-RUNNER-A",
        "run:c2",
    ]


# -----------------------------------------------------------------------------
# End to end — a real, migrated, freshly seeded Postgres and the real running app
# -----------------------------------------------------------------------------


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
            "customer_status) VALUES ('CLI-RUNNER-A', 'First', 'Last', 'México', 'Active')"
        )
    return value


def _settings(**updates: Any) -> Settings:
    values: dict[str, Any] = {
        "session_signing_key": SecretStr(SIGNING_KEY),
        "test_identity_enabled": True,
        "test_identity_key": SecretStr(LOGIN_KEY),
        "llm_provider": LlmProvider.STUB,
        "service_version": "test-sha",
        "data_as_of_date": "2026-06-18",
    }
    return load_settings(env_file=None).model_copy(update={**values, **updates})


@pytest.mark.integration
def test_run_cases_end_to_end_against_the_real_running_system(dsn: str) -> None:
    """No controller, tool port, retriever or customer-lookup override anywhere in this test:
    the real application, built exactly as it would be for a deployment, minus a real LLM call
    (the stub provider) so this runs in CI without a model API key."""
    settings = _settings(database_url=SecretStr(dsn))
    app = create_app(settings)
    client = TestClient(app)
    case = _case()

    results = run_cases(client, dsn, (case,), test_login_key=LOGIN_KEY)

    assert len(results) == 1
    result = results[0]
    assert result.case_id == case.case_id
    assert result.correct_outcome is True
    assert result.observed_escalation is False
    assert result.is_unsafe is False
    assert result.latency_seconds is not None
    assert result.latency_seconds >= 0


@pytest.mark.integration
def test_run_cases_end_to_end_with_capture_transcripts_fills_the_two_fields(dsn: str) -> None:
    settings = _settings(database_url=SecretStr(dsn))
    app = create_app(settings)
    client = TestClient(app)
    case = _case()

    results = run_cases(client, dsn, (case,), test_login_key=LOGIN_KEY, capture_transcripts=True)

    assert len(results) == 1
    result = results[0]
    assert result.reply_text
    assert result.facts_and_sources
    assert "filing-windows" in result.facts_and_sources


@pytest.mark.integration
def test_run_cases_end_to_end_reports_a_wrong_language_reply_as_incorrect(dsn: str) -> None:
    settings = _settings(database_url=SecretStr(dsn))
    app = create_app(settings)
    client = TestClient(app)
    case = _case(lang="pt", user_turns=("¿Cuánto tiempo tengo para presentar una disputa?",))

    results = run_cases(client, dsn, (case,), test_login_key=LOGIN_KEY)

    assert results[0].correct_outcome is False
