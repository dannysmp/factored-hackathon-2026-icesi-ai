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
import os
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
from contracts.service_v1.envelope import Intent
from evals.metrics import CaseResult
from evals.models import Case, CaseCategory
from evals.runner.runner import run_cases

LOGIN_KEY = "test-login-key-0123456789"
SIGNING_KEY = "s" * 40


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
def test_run_cases_end_to_end_reports_a_wrong_language_reply_as_incorrect(dsn: str) -> None:
    settings = _settings(database_url=SecretStr(dsn))
    app = create_app(settings)
    client = TestClient(app)
    case = _case(lang="pt", user_turns=("¿Cuánto tiempo tengo para presentar una disputa?",))

    results = run_cases(client, dsn, (case,), test_login_key=LOGIN_KEY)

    assert results[0].correct_outcome is False
