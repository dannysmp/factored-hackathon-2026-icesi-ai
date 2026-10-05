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
from app.config import AppEnvironment, ConfigError, LlmProvider, Settings, load_settings
from app.persistence.migrate import apply_migrations
from app.security.sessions import Principal
from contracts.service_v1.envelope import Intent
from contracts.service_v1.tools import ToolPort
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


def test_build_b0_app_hands_the_tool_port_decorator_to_the_application_it_builds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, Any] = {}

    def fake_create_app(settings: Settings, **kwargs: Any) -> str:
        received.update(kwargs)
        return "app"

    def decorator(principal: Principal, port: ToolPort) -> ToolPort:
        return port

    monkeypatch.setattr("evals.runner.baselines.b0.create_app", fake_create_app)

    build_b0_app(load_settings(env_file=None), tool_port_decorator=decorator)

    assert received == {"tool_port_decorator": decorator}


def test_build_b0_app_refuses_when_app_env_is_prod() -> None:
    """A ``Settings`` object with ``app_env=prod`` is refused even though ``model_copy`` never
    revalidates ``Settings``'s own ``llm_provider=stub``-in-prod rule; a settings object built
    this way (rather than through ``load_settings``) must not be able to sail past it."""
    settings = load_settings(env_file=None).model_copy(
        update={
            "app_env": AppEnvironment.PROD,
            "llm_provider": LlmProvider.ANTHROPIC,
            "service_version": "test-sha",
            "data_as_of_date": "2026-06-18",
        }
    )

    with pytest.raises(ConfigError, match="not allowed when APP_ENV=prod"):
        build_b0_app(settings)


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
def test_b0_answers_a_policy_question_end_to_end(dsn: str) -> None:
    """B0's own end-to-end shape: real store, real controller, real retriever and policy —
    exactly as B0 is defined (a deterministic classifier, no LLM). This proves the case is answered
    correctly; it does not by itself prove no LLM call happened (a real network call could succeed
    or fail and this case would still score correctly either way) — that structural property has
    its own test, below."""
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
        expected_policy_section_id="filing-windows",
    )

    results = run_cases(client, dsn, (case,), test_login_key=LOGIN_KEY)

    assert len(results) == 1
    assert results[0].correct_outcome is True


@pytest.mark.integration
def test_b0_never_constructs_a_real_anthropic_client(
    dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structural proof that B0 makes no LLM call: the Anthropic SDK's own client class is
    patched to fail immediately if anything ever tries to construct one. A passing run here means
    the code path that would build a real client was never reached at all, not merely that a call
    from it went unobserved."""

    def _must_not_construct(*args: object, **kwargs: object) -> object:
        raise AssertionError("B0 must never construct a real Anthropic client")

    monkeypatch.setattr("anthropic.Anthropic", _must_not_construct)

    settings = _settings(database_url=SecretStr(dsn))
    client = TestClient(build_b0_app(settings))
    case = Case(
        case_id="b0-test-03",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="observed",
        seed_ref="ops_seed:CLI-B0-A",
        user_turns=("¿Cuánto tiempo tengo para presentar una disputa?",),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="filing-windows",
    )

    results = run_cases(client, dsn, (case,), test_login_key=LOGIN_KEY)

    assert results[0].correct_outcome is True
