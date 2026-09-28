"""
B0 Baseline Tests
==================

Component: ``evals.runner.baselines.b0``. Building the app when no sign-in path is enabled needs
no database at all; proving B0 actually answers a case correctly, using the deterministic
classifier and never a real model call, needs a real, migrated Postgres and the real running
application. The latter is marked ``integration``, skipped when ``DATABASE_URL`` is not set,
following this project's own convention — the same end-to-end shape ``tests.test_evals_runner``
already proved for P, reused here through ``build_b0_app`` instead of a hand-set ``llm_provider``.
"""

from __future__ import annotations

# Standard libraries
import os
from typing import Any

# Third-party libraries
import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

# Local modules
from app.config import LlmProvider, Settings, load_settings
from app.persistence.migrate import apply_migrations
from contracts.service_v1.envelope import Intent
from evals.models import Case, CaseCategory
from evals.runner.baselines.b0 import build_b0_app
from evals.runner.runner import run_cases

LOGIN_KEY = "test-login-key-0123456789"
SIGNING_KEY = "s" * 40


def test_build_b0_app_needs_no_database_when_no_sign_in_path_is_enabled() -> None:
    """Constructing the app is decoupled from resolving a real database connection; only an
    actual request (the integration tests below) exercises the store at all."""
    settings = load_settings(env_file=None).model_copy(
        update={
            "llm_provider": LlmProvider.ANTHROPIC,
            "service_version": "test-sha",
            "data_as_of_date": "2026-06-18",
        }
    )
    assert settings.llm_provider is LlmProvider.ANTHROPIC  # sanity: the input is a real provider

    app = build_b0_app(settings)

    assert app is not None


# -----------------------------------------------------------------------------
# End to end — B0 answers a real case against a real, migrated Postgres
# -----------------------------------------------------------------------------


def _settings(**updates: Any) -> Settings:
    values: dict[str, Any] = {
        "session_signing_key": SecretStr(SIGNING_KEY),
        "test_identity_enabled": True,
        "test_identity_key": SecretStr(LOGIN_KEY),
        "llm_provider": LlmProvider.ANTHROPIC,
        "anthropic_api_key": SecretStr("unused-would-fail-if-ever-called"),
        "service_version": "test-sha",
        "data_as_of_date": "2026-06-18",
    }
    return load_settings(env_file=None).model_copy(update={**values, **updates})


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
            "customer_status) VALUES ('CLI-B0-A', 'First', 'Last', 'México', 'Active')"
        )
    return value


@pytest.mark.integration
def test_b0_answers_a_policy_question_end_to_end_with_no_real_model_key(dsn: str) -> None:
    """B0's own end-to-end shape: real store, real controller, real retriever and policy — no
    LLM call, the deterministic classifier only, exactly as the evaluation plan defines B0. The
    configured Anthropic key is a value that would fail if the adapter ever tried to use it, so a
    passing run is itself proof no model call happened."""
    settings = _settings(database_url=SecretStr(dsn))
    client = TestClient(build_b0_app(settings))
    case = Case(
        case_id="b0-test-01",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="observed",
        seed_ref="ops_seed:CLI-B0-A",
        user_turns=("¿Cuánto tiempo tengo para presentar una disputa?",),
        expected_intent=Intent.POLICY_ANSWER,
    )

    results = run_cases(client, dsn, (case,), test_login_key=LOGIN_KEY)

    assert len(results) == 1
    assert results[0].correct_outcome is True
