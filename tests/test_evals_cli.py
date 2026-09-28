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
from datetime import UTC, date, datetime

import psycopg
import pytest
from pydantic import SecretStr

import evals.cli
from app.config import ConfigError, LlmProvider, load_settings
from app.domain.calendar import DomainCalendar
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
# _resolve_calendar and _run_b1's own wiring — hermetic
# -----------------------------------------------------------------------------


def test_resolve_calendar_uses_the_explicit_setting_when_present() -> None:
    settings = load_settings(env_file=None).model_copy(
        update={"data_as_of_date": "2026-06-18", "database_url": None}
    )

    calendar = evals.cli._resolve_calendar(settings, clock=lambda: datetime(2099, 1, 1, tzinfo=UTC))

    assert calendar.reference_date == date(2026, 6, 18)


def test_resolve_calendar_raises_when_no_source_resolves_a_date() -> None:
    settings = load_settings(env_file=None).model_copy(
        update={"data_as_of_date": None, "database_url": None}
    )

    with pytest.raises(ConfigError, match="No domain date resolves"):
        evals.cli._resolve_calendar(settings, clock=lambda: datetime(2099, 1, 1, tzinfo=UTC))


class _CapturedNaiveAgentClient:
    """Stands in for NaiveAgentClient in _run_b1's wiring test: records what it was built with
    instead of touching the real Anthropic SDK."""

    def __init__(self, api_key: SecretStr, *, model: str) -> None:
        self.api_key = api_key
        self.model = model


def test_run_b1_wires_the_model_dsn_and_calendar_correctly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves _run_b1's own wiring — the model id it picks, the dsn it resolves, the calendar it
    builds — without ever reaching the real Anthropic API or a real Postgres: both
    NaiveAgentClient and run_b1_cases are replaced with recorders."""
    captured: dict[str, object] = {}

    def fake_run_b1_cases(
        client: object,
        settings: object,
        dsn: str,
        cases: object,
        *,
        policy: object,
        retriever: object,
        calendar: object,
        clock: object,
    ) -> tuple[CaseResult, ...]:
        captured["client"] = client
        captured["dsn"] = dsn
        captured["cases"] = cases
        captured["calendar"] = calendar
        return ()

    monkeypatch.setattr(evals.cli, "NaiveAgentClient", _CapturedNaiveAgentClient)
    monkeypatch.setattr(evals.cli, "run_b1_cases", fake_run_b1_cases)
    settings = load_settings(env_file=None).model_copy(
        update={
            "database_url": SecretStr("postgresql://unused-dsn"),
            "anthropic_api_key": SecretStr("sk-test-unused"),
            "data_as_of_date": "2026-06-18",
        }
    )
    cases = ()

    result = evals.cli._run_b1(settings, cases)

    assert result == ()
    assert captured["dsn"] == "postgresql://unused-dsn"
    assert captured["cases"] is cases
    client = captured["client"]
    assert isinstance(client, _CapturedNaiveAgentClient)
    assert client.model == settings.nlu_model
    calendar = captured["calendar"]
    assert isinstance(calendar, DomainCalendar)
    assert calendar.reference_date == date(2026, 6, 18)


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


def _set_smoke_env(monkeypatch: pytest.MonkeyPatch, dsn: str) -> None:
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("SESSION_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("TEST_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("TEST_IDENTITY_KEY", LOGIN_KEY)
    monkeypatch.setenv("SERVICE_VERSION", "test-sha")
    monkeypatch.setenv("DATA_AS_OF_DATE", "2026-06-18")
    monkeypatch.setenv("LLM_PROVIDER", LlmProvider.STUB.value)


@pytest.mark.integration
def test_main_runs_the_smoke_slice_against_b0_end_to_end(
    dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole pipeline this PR builds, together: seed the CI-only synthetic data, then run
    the real B0 baseline (deterministic classifier, no LLM) over the smoke slice through the
    same CLI `make evaluate` will call. No case here may score unsafe — that is the CI gate this
    module exists to enforce."""
    seed_ci_smoke_data(dsn)
    _set_smoke_env(monkeypatch, dsn)

    exit_code = main(["--system", "B0", "--smoke"])

    assert exit_code == 0


@pytest.mark.integration
def test_main_runs_the_smoke_slice_against_p_end_to_end(
    dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same pipeline, but for the proposed system itself — the CLI's _run_p wiring
    (create_app, TestClient, the sandbox login) exercised for real, over a stubbed LLM so this
    stays network-free. No case here may score unsafe either."""
    seed_ci_smoke_data(dsn)
    _set_smoke_env(monkeypatch, dsn)

    exit_code = main(["--system", "P", "--smoke"])

    assert exit_code == 0
