"""
Evaluation CLI Tests
======================

Component: ``evals.cli``. Formatting, case selection and the login-key precondition are
hermetic. ``main``'s exit-code rule is proven hermetically against a faked runner. The real,
end-to-end smoke run against B0 needs a real, migrated Postgres and the CI-only synthetic seed;
marked ``integration``, skipped when ``DATABASE_URL`` is not set.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import duckdb
import psycopg
import pytest
from pydantic import SecretStr

import evals.cli
from app.config import ConfigError, LlmProvider, load_settings
from app.domain.calendar import DateOrigin, DomainCalendar
from app.domain.policy.models import DisputeCategory
from app.persistence.migrate import apply_migrations
from contracts.service_v1.envelope import Intent
from evals.cli import _fmt, _require_test_login_key, _select_cases, main
from evals.fairness import CaseProfile
from evals.golden.case_sheet import ALL_CASES
from evals.judge import JudgeVerdict, LlmJudge
from evals.metrics import NOT_DEFINED, CaseResult, Metric
from evals.models import Case, CaseCategory
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
# --full — hermetic against faked runners and a faked calendar
# -----------------------------------------------------------------------------


def _full_result(
    case_id: str,
    *,
    is_unsafe: bool = False,
    correct_outcome: bool = True,
    error: str | None = None,
) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        is_adversarial=False,
        expected_escalation=False,
        observed_escalation=False,
        automation_attempted=True,
        correct_outcome=correct_outcome,
        automated_success=correct_outcome,
        is_unsafe=is_unsafe,
        error=error,
    )


def _case(**overrides: Any) -> Case:
    values: dict[str, Any] = {
        "case_id": "c1",
        "category": CaseCategory.NORMAL,
        "lang": "es",
        "provenance": "observed",
        "seed_ref": "ops_seed:TRX-1",
        "user_turns": ("No reconozco un cargo.",),
        "expected_intent": Intent.CONFIRM_FILING,
        "expected_category": DisputeCategory.UNRECOGNIZED_CHARGE,
    }
    return Case(**{**values, **overrides})


# Never actually called in these hermetic tests: every ``_full_result`` leaves ``reply_text``
# unset, so ``_score_with_judge`` skips every case before it would reach ``judge.score(...)``.
_UNUSED_JUDGE = cast(LlmJudge, None)


class _CapturedAnthropicLlmClient:
    """Stands in for AnthropicLlmClient in the ``--full`` hermetic tests: records what it was
    built with instead of touching the real Anthropic SDK, matching
    ``_CapturedNaiveAgentClient``'s own pattern below."""

    def __init__(self, api_key: SecretStr) -> None:
        self.api_key = api_key


def _patch_full_report_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    p_runs: list[tuple[CaseResult, ...]],
    b0_run: tuple[CaseResult, ...] = (),
    b1_run: tuple[CaseResult, ...] = (),
) -> None:
    fake_settings = SimpleNamespace(
        nlu_model="claude-haiku-4-5-20251001",
        render_model="claude-sonnet-5",
        judge_model="claude-sonnet-5",
        require_anthropic_key=lambda: SecretStr("sk-test-unused"),
        require_database_url=lambda: SecretStr("postgresql://unused"),
    )
    monkeypatch.setattr(evals.cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(evals.cli, "load_case_profiles", lambda dsn, cases, *, silver_dir: {})
    monkeypatch.setattr(evals.cli, "AnthropicLlmClient", _CapturedAnthropicLlmClient)
    monkeypatch.setattr(
        evals.cli,
        "_resolve_calendar",
        lambda settings, *, clock: DomainCalendar(date(2026, 6, 18), DateOrigin.SETTING),
    )
    p_iterator = iter(p_runs)
    monkeypatch.setitem(evals.cli._RUNNERS, "P", lambda settings, cases, **kwargs: next(p_iterator))
    monkeypatch.setitem(evals.cli._RUNNERS, "B0", lambda settings, cases: b0_run)
    monkeypatch.setitem(evals.cli._RUNNERS, "B1", lambda settings, cases: b1_run)


def test_build_system_result_reports_no_flips_for_a_single_run() -> None:
    result = evals.cli._build_system_result(
        "B0", [(_full_result("c1"),)], (_case(case_id="c1"),), _UNUSED_JUDGE
    )

    assert result.run_count == 1
    assert result.flips == ()
    assert result.case_results == (_full_result("c1"),)
    assert result.judge_verdicts == ()  # B0 is never in _JUDGED_SYSTEMS


def test_build_system_result_reports_flips_across_repeated_runs() -> None:
    runs = [
        (_full_result("c1", correct_outcome=True),),
        (_full_result("c1", correct_outcome=False),),
        (_full_result("c1", correct_outcome=True),),
    ]

    result = evals.cli._build_system_result("P", runs, (_case(case_id="c1"),), _UNUSED_JUDGE)

    assert result.run_count == 3
    assert len(result.flips) == 1
    assert result.flips[0].case_id == "c1"
    # The failure gallery shows only the last run, not an arbitrary earlier one.
    assert result.case_results == runs[-1]
    # None of these results captured a transcript, so nothing reaches the judge at all.
    assert result.judge_verdicts == ()


class _RecordingJudge:
    """Stands in for ``LlmJudge``: records every ``score(...)`` call and returns a fixed verdict
    naming the case, so a test can tell exactly which cases reached the judge and with what."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def score(self, case_id: str, **kwargs: Any) -> JudgeVerdict:
        self.calls.append({"case_id": case_id, **kwargs})
        return JudgeVerdict(
            case_id=case_id,
            grounding=2,
            language_quality=2,
            clarification=None,
            rationale="fixed",
            judge_model="claude-sonnet-5",
            prompt_version="1",
        )


def _captured(
    case_id: str, *, reply: str | None = "Listo.", facts: str | None = "facts"
) -> CaseResult:
    return dataclasses.replace(_full_result(case_id), reply_text=reply, facts_and_sources=facts)


def test_score_with_judge_sends_a_captured_case_its_transcript_and_grounding() -> None:
    judge = _RecordingJudge()
    case = _case(case_id="c1", lang="pt", user_turns=("Não reconheço.",))

    verdicts = evals.cli._score_with_judge(
        cast(LlmJudge, judge), (case,), (_captured("c1", reply="Pronto.", facts="F1"),)
    )

    assert [verdict.case_id for verdict in verdicts] == ["c1"]
    assert judge.calls == [
        {
            "case_id": "c1",
            "language": "pt",
            "user_turns": ("Não reconheço.",),
            "system_replies": ("Pronto.",),
            "facts_and_sources": "F1",
        }
    ]


def test_score_with_judge_skips_a_case_with_no_result_or_an_incomplete_capture() -> None:
    """A gap in capture must stay a gap, never be scored against empty text."""
    judge = _RecordingJudge()
    cases = tuple(_case(case_id=case_id) for case_id in ("c1", "c2", "c3", "c4"))
    results = (
        _captured("c1"),
        _captured("c2", reply=None),
        _captured("c3", facts=None),
    )  # c4 has no result at all

    verdicts = evals.cli._score_with_judge(cast(LlmJudge, judge), cases, results)

    assert [verdict.case_id for verdict in verdicts] == ["c1"]
    assert [call["case_id"] for call in judge.calls] == ["c1"]


def test_score_with_judge_matches_results_by_case_id_not_position() -> None:
    judge = _RecordingJudge()
    cases = (_case(case_id="c1"), _case(case_id="c2"))
    results = (_captured("c2", reply="reply-2"), _captured("c1", reply="reply-1"))

    evals.cli._score_with_judge(cast(LlmJudge, judge), cases, results)

    assert [(call["case_id"], call["system_replies"]) for call in judge.calls] == [
        ("c1", ("reply-1",)),
        ("c2", ("reply-2",)),
    ]


def test_build_system_result_judges_only_p_and_only_its_last_run() -> None:
    judge = _RecordingJudge()
    cases = (_case(case_id="c1"),)
    runs = [(_captured("c1", reply="first"),), (_captured("c1", reply="last"),)]

    p_result = evals.cli._build_system_result("P", runs, cases, cast(LlmJudge, judge))
    b1_result = evals.cli._build_system_result("B1", runs[-1:], cases, cast(LlmJudge, judge))

    assert [verdict.case_id for verdict in p_result.judge_verdicts] == ["c1"]
    assert [call["system_replies"] for call in judge.calls] == [("last",)]
    assert b1_result.judge_verdicts == ()


def test_full_and_system_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        main(["--system", "B0", "--full"])


def test_neither_full_nor_system_is_an_error() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_full_writes_the_report_and_exits_0_when_nothing_is_unsafe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_full_report_dependencies(
        monkeypatch,
        p_runs=[(_full_result("c1"),), (_full_result("c1"),), (_full_result("c1"),)],
        b0_run=(_full_result("c1"),),
        b1_run=(_full_result("c1"),),
    )
    report_path = tmp_path / "evaluation.md"

    exit_code = main(["--full", "--report", str(report_path)])

    assert exit_code == 0
    text = report_path.read_text(encoding="utf-8")
    assert "# Evaluation Report" in text
    assert "2026-06-18" in text
    assert "Scope." not in text  # a full-golden-set run carries no scope_note


def test_full_gives_the_report_each_cases_customer_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_full_report_dependencies(
        monkeypatch,
        p_runs=[(_full_result("c1"),)] * 3,
        b0_run=(_full_result("c1"),),
        b1_run=(_full_result("c1"),),
    )
    seen: dict[str, object] = {}

    def _profiles(dsn: str, cases: Sequence[Case], *, silver_dir: Path) -> dict[str, CaseProfile]:
        seen["dsn"] = dsn
        seen["silver_dir"] = silver_dir
        return {"c1": CaseProfile(country="MX", segment="Plus")}

    monkeypatch.setattr(evals.cli, "load_case_profiles", _profiles)
    report_path = tmp_path / "evaluation.md"

    assert main(["--full", "--report", str(report_path)]) == 0

    text = report_path.read_text(encoding="utf-8")
    assert seen["dsn"] == "postgresql://unused"
    silver_dir = seen["silver_dir"]
    assert isinstance(silver_dir, Path)
    assert silver_dir.is_absolute()
    assert silver_dir == Path(evals.cli.__file__).resolve().parents[1] / "data" / "silver"
    assert "| country | MX | 1 |" in text
    assert "| segment | Plus | 1 |" in text
    assert "could not be looked up" not in text


@pytest.mark.parametrize(
    "failure",
    [
        psycopg.OperationalError("store unreachable"),
        duckdb.IOException("cleaned table unreadable"),
        ConfigError("DATABASE_URL is not set"),
    ],
    ids=["store", "cleaned-table", "settings"],
)
def test_full_still_writes_the_report_when_the_profile_lookup_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: Exception
) -> None:
    _patch_full_report_dependencies(
        monkeypatch,
        p_runs=[(_full_result("c1"),)] * 3,
        b0_run=(_full_result("c1"),),
        b1_run=(_full_result("c1"),),
    )

    def _unreachable(
        dsn: str, cases: Sequence[Case], *, silver_dir: Path
    ) -> dict[str, CaseProfile]:
        raise failure

    monkeypatch.setattr(evals.cli, "load_case_profiles", _unreachable)
    report_path = tmp_path / "evaluation.md"

    assert main(["--full", "--report", str(report_path)]) == 0

    assert "could not be looked up" in report_path.read_text(encoding="utf-8")


def test_full_smoke_narrows_the_case_set_and_discloses_it_in_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--full --smoke deliberately narrows a full run to the 16-case CI-smoke subset for a faster
    check; the written report must disclose the narrowing, never silently under-report as if it
    were the full 135-case golden set."""
    captured_cases: dict[str, tuple[Case, ...]] = {}

    def _capturing_runner(
        system: str,
    ) -> Callable[..., tuple[CaseResult, ...]]:
        def runner(
            settings: object, cases: Sequence[Case], **kwargs: object
        ) -> tuple[CaseResult, ...]:
            captured_cases[system] = tuple(cases)
            return (_full_result("c1"),)

        return runner

    _patch_full_report_dependencies(
        monkeypatch,
        p_runs=[(_full_result("c1"),), (_full_result("c1"),), (_full_result("c1"),)],
        b0_run=(_full_result("c1"),),
        b1_run=(_full_result("c1"),),
    )
    for system in ("P", "B0", "B1"):
        monkeypatch.setitem(evals.cli._RUNNERS, system, _capturing_runner(system))
    report_path = tmp_path / "evaluation.md"

    exit_code = main(["--full", "--smoke", "--report", str(report_path)])

    assert exit_code == 0
    for system in ("P", "B0", "B1"):
        assert {case.case_id for case in captured_cases[system]} == SMOKE_CASE_IDS
    text = report_path.read_text(encoding="utf-8")
    assert "**Scope.**" in text
    assert "16-case CI-smoke subset" in text
    assert "--smoke was passed" in text
    assert f"Total golden-set cases: {len(SMOKE_CASE_IDS)}" in text


def test_full_exits_1_when_any_run_of_any_system_is_unsafe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The exit code must catch an unsafe case even in a P run later discarded from the report's
    own displayed case_results (only the last of the three runs is kept for the failure gallery)."""
    _patch_full_report_dependencies(
        monkeypatch,
        p_runs=[
            (_full_result("c1", is_unsafe=True),),
            (_full_result("c1"),),
            (_full_result("c1"),),
        ],
        b0_run=(_full_result("c1"),),
        b1_run=(_full_result("c1"),),
    )
    report_path = tmp_path / "evaluation.md"

    exit_code = main(["--full", "--report", str(report_path)])

    assert exit_code == 1


def test_full_report_gives_an_errored_case_its_own_failure_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ``CaseResult.error`` must render as its own failure class in the failure gallery, not
    blend into "incorrect outcome" or "unsafe" — those are different facts about a case."""
    _patch_full_report_dependencies(
        monkeypatch,
        p_runs=[
            (_full_result("c1"),),
            (_full_result("c1"),),
            (_full_result("c1", correct_outcome=False, error="ValueError: bad seed_ref"),),
        ],
        b0_run=(_full_result("c1"),),
        b1_run=(_full_result("c1"),),
    )
    report_path = tmp_path / "evaluation.md"

    exit_code = main(["--full", "--report", str(report_path)])

    assert exit_code == 0
    text = report_path.read_text(encoding="utf-8")
    assert "| P | c1 | error | ValueError: bad seed_ref |" in text
    assert "incorrect outcome" not in text


def test_full_logs_an_errored_case_even_from_a_run_discarded_by_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An error in a P run other than the last (which the report's own case_results discards) must
    still be logged — the same completeness the exit code already gives ``is_unsafe``."""
    _patch_full_report_dependencies(
        monkeypatch,
        p_runs=[
            (_full_result("c1", error="ValueError: bad seed_ref"),),
            (_full_result("c1"),),
            (_full_result("c1"),),
        ],
        b0_run=(_full_result("c1"),),
        b1_run=(_full_result("c1"),),
    )
    report_path = tmp_path / "evaluation.md"

    with caplog.at_level(logging.ERROR):
        exit_code = main(["--full", "--report", str(report_path)])

    assert exit_code == 0
    assert "errored_cases system=P" in caplog.text


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
    """The whole pipeline, together: seed the CI-only synthetic data, then run the real B0
    baseline (deterministic classifier, no LLM) over the smoke subset through the same CLI
    `make evaluate` calls. No case here may score unsafe — that is the CI gate this module exists
    to enforce."""
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
