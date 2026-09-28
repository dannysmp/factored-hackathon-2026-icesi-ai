"""
Evaluation CLI Tests
======================

Component: ``evals.cli``. Formatting, case selection and the login-key precondition are
hermetic. ``main``'s exit-code rule is proven hermetically against a faked runner. The real,
end-to-end smoke run against B0 needs a real, migrated Postgres and the CI-only synthetic seed;
marked ``integration``, skipped when ``DATABASE_URL`` is not set.
"""

from __future__ import annotations

import os

import psycopg
import pytest
from pydantic import SecretStr

import evals.cli
from app.config import ConfigError, LlmProvider, load_settings
from app.persistence.migrate import apply_migrations
from evals.cli import _fmt, _require_test_login_key, _select_cases, main
from evals.golden.case_sheet import ALL_CASES
from evals.metrics import NOT_DEFINED, CaseResult, Metric
from evals.runner.smoke import SMOKE_CASE_IDS
from tests.fixtures.ci_smoke_seed import seed_ci_smoke_data

LOGIN_KEY = "test-login-key-0123456789"
SIGNING_KEY = "session-signing-key-with-enough-distinct-chars-0123456789"

# -----------------------------------------------------------------------------
# Formatting and case selection — hermetic
# -----------------------------------------------------------------------------


def test_fmt_renders_a_defined_metric_with_its_denominator() -> None:
    assert _fmt(Metric(0.5, basis="measured", denominator=8)) == "0.500(n=8)"


def test_fmt_renders_a_not_defined_metric_without_a_percentage() -> None:
    assert _fmt(Metric(NOT_DEFINED, basis="measured", denominator=0)) == "not_defined(n=0)"


def test_select_cases_smoke_returns_exactly_the_smoke_set() -> None:
    cases = _select_cases(smoke=True)

    assert {case.case_id for case in cases} == SMOKE_CASE_IDS


def test_select_cases_full_returns_the_whole_golden_set() -> None:
    assert _select_cases(smoke=False) == ALL_CASES


# -----------------------------------------------------------------------------
# The sandbox-login precondition — hermetic
# -----------------------------------------------------------------------------


def test_require_test_login_key_raises_when_the_sandbox_login_is_not_enabled() -> None:
    settings = load_settings(env_file=None).model_copy(
        update={"test_identity_enabled": False, "test_identity_key": None}
    )

    with pytest.raises(ConfigError, match="sandbox login"):
        _require_test_login_key(settings)


def test_require_test_login_key_returns_the_key_when_already_configured() -> None:
    settings = load_settings(env_file=None).model_copy(
        update={"test_identity_enabled": True, "test_identity_key": SecretStr(LOGIN_KEY)}
    )

    assert _require_test_login_key(settings) == LOGIN_KEY


# -----------------------------------------------------------------------------
# main()'s exit code — hermetic against a faked runner
# -----------------------------------------------------------------------------


def _result(case_id: str, *, is_unsafe: bool) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        is_adversarial=True,
        expected_escalation=False,
        observed_escalation=False,
        automation_attempted=False,
        correct_outcome=True,
        is_unsafe=is_unsafe,
    )


def test_main_exits_0_when_no_case_is_unsafe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evals.cli, "load_settings", object)
    monkeypatch.setitem(
        evals.cli._RUNNERS,
        "B0",
        lambda settings, cases: (_result("c1", is_unsafe=False), _result("c2", is_unsafe=False)),
    )

    assert main(["--system", "B0", "--smoke"]) == 0


def test_main_exits_1_when_any_case_is_unsafe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evals.cli, "load_settings", object)
    monkeypatch.setitem(
        evals.cli._RUNNERS,
        "B0",
        lambda settings, cases: (_result("c1", is_unsafe=False), _result("c2", is_unsafe=True)),
    )

    assert main(["--system", "B0", "--smoke"]) == 1


def test_main_rejects_an_unknown_system() -> None:
    with pytest.raises(SystemExit):
        main(["--system", "B2"])


# -----------------------------------------------------------------------------
# End to end — a real, migrated, freshly seeded Postgres, B0 over real HTTP
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
    return value


@pytest.mark.integration
def test_main_runs_the_smoke_slice_against_b0_end_to_end(
    dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole pipeline this PR builds, together: seed the CI-only synthetic data, then run
    the real B0 baseline (deterministic classifier, no LLM) over the smoke slice through the
    same CLI `make evaluate` will call. No case here may score unsafe — that is the CI gate this
    module exists to enforce."""
    seed_ci_smoke_data(dsn)
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("SESSION_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("TEST_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("TEST_IDENTITY_KEY", LOGIN_KEY)
    monkeypatch.setenv("SERVICE_VERSION", "test-sha")
    monkeypatch.setenv("DATA_AS_OF_DATE", "2026-06-18")
    monkeypatch.setenv("LLM_PROVIDER", LlmProvider.STUB.value)

    exit_code = main(["--system", "B0", "--smoke"])

    assert exit_code == 0
