"""
Evaluation CLI
==============

Overview
--------
Runs the golden set against the system variants and reports the result. Two modes, both accepting
``--smoke`` to narrow the case set to ``evals.runner.smoke.smoke_cases()`` instead of the full
golden set, ``evals.golden.case_sheet.ALL_CASES``:

- ``make evaluate SYSTEM={P|B0|B1} [SMOKE=1]`` runs one system variant once and logs its headline
  metrics; this is the run the continuous-integration smoke job gates on.
- ``make evaluate FULL=1 [SMOKE=1]`` runs every system variant (P three times, B0 and B1 once each)
  and writes the single generated report, ``reports/evaluation.md``. Under ``--smoke`` the report's
  ``scope_note`` discloses the narrower case set rather than silently under-reporting. The full
  mode also scores P's last run with the live judge (``_JUDGED_SYSTEMS``), which feeds the report's
  judge-scored-quality section; B0 and B1 carry no judge verdicts.

Scope
-----
In: choosing and building the dependencies of each requested variant, running the batches, logging
a summary or writing the full report, scoring P's last full-mode run with the live judge, and the
process exit code.
Out: loading seed data into the target store, which the caller does first (``make load-seed``
for ``data/gold/ops_seed``, then ``make load-eval-bank`` for the adversarial cases that need the
evaluation bank; the smoke subset needs only the operational seed). Also out: the agreement-with-
human section of the report, which reads a separate rater sample (see ``evals.judge_validation``) —
a live judge call over P's run answers "how good is this run", not "how well does the judge agree
with a human".

Design Principles
-----------------
- **P and B0 drive the real HTTP surface, in process.** Both are built with
  ``app.main.create_app`` (B0 via ``evals.runner.baselines.b0.build_b0_app``, which also forces a
  stubbed LLM) and driven through ``starlette.testclient.TestClient``: no network socket and no
  second HTTP client construction path.
- **B1 never touches the HTTP surface.** Its batch runner (``evals.runner.baselines.b1.run_cases``)
  builds a real ``NaiveAgentClient`` against a real Anthropic key. B1 exists to measure what an
  unsupervised model does, so it has no stub path, which is also why it does not run in the
  continuous-integration smoke job.
- **The sandbox login must already be enabled; this module never turns it on.** P and B0
  authenticate through ``POST /v1/auth/test-sessions``, which exists only when
  ``settings.test_identity_enabled`` is true. Forcing it on here, as ``build_b0_app`` forces
  ``llm_provider``, would let a command-line invocation expose a passwordless login the operator's
  configuration did not ask for; the module fails loudly instead, naming the two settings.
- **B1 calls the model named by ``settings.nlu_model``.** B1 is a single unified agent role, so
  neither of P's two configured models (understanding, rendering) maps onto it exactly; B1's
  defining behaviour is choosing the next tool, which is closest to the understanding role. See the
  Limitations of ``evals.runner.baselines.b1``.
- **The exit code is the enforcement mechanism.** A batch with any ``CaseResult.is_unsafe`` exits
  ``1``, so a regression that turns any case unsafe fails the job that ran it.
- **A judge call that cannot complete aborts the full run.** ``evals.judge`` lets any ``LlmError``
  propagate because no customer waits on a judge call; this module does not swallow it and
  continue.

Runtime Contract
----------------
``main(argv) -> int``. Command line: ``python -m evals.cli --system {P,B0,B1} [--smoke]`` or
``python -m evals.cli --full [--smoke] [--report PATH]`` (default ``reports/evaluation.md``);
exactly one of ``--system`` and ``--full`` is required. The exit code is ``1`` when any case in any
run was unsafe, otherwise ``0``; a missing prerequisite (database URL, API key, sandbox login)
raises ``ConfigError`` instead.

Limitations
-----------
A case that fails to resolve, run or score with one of the documented failure classes of
``evals.runner.runner`` or ``evals.runner.baselines.b1`` does not abort the batch: it is recorded
as a named ``CaseResult.error`` and the batch continues, independently for each batch of a full
run. An exception outside those classes propagates and stops the run. The agreement-with-human
section reads its rater sample once and is not recomputed per system. The live judge scores P's
last run only, and only the cases for which that run captured a transcript (see the Limitations of
``evals.runner.runner``); a case the capture missed is absent from the judge-scored-quality
section's denominator rather than reported as a zero.
"""

from __future__ import annotations

# Standard libraries
import argparse
import logging
from collections.abc import Callable, Sequence
from pathlib import Path

# Third-party libraries
import duckdb
import psycopg
from starlette.testclient import TestClient

# Local modules
from app.config import ConfigError, Settings, load_settings
from app.domain.calendar import DomainCalendar, DomainCalendarError, resolve_domain_calendar
from app.domain.policy.loader import load_policy
from app.llm.anthropic_client import AnthropicLlmClient
from app.llm.prompts import load_prompt
from app.main import create_app
from app.persistence.ops_meta import read_data_as_of
from app.retrieval.lexical import LexicalRetriever
from app.security.sessions import Clock
from app.security.sessions import utc_now as _real_clock
from evals.cost import CostTrackingLlm, TurnCostLedger
from evals.fairness import CaseProfile
from evals.golden.case_sheet import ALL_CASES
from evals.golden.judge_validation_sample import (
    JUDGE_VERDICTS as _SYNTHETIC_JUDGE_VERDICTS,
)
from evals.golden.judge_validation_sample import (
    PROVENANCE as _SYNTHETIC_JUDGE_VALIDATION_PROVENANCE,
)
from evals.golden.judge_validation_sample import (
    RATER_1_SCORES as _SYNTHETIC_RATER_1_SCORES,
)
from evals.golden.judge_validation_sample import (
    RATER_2_SCORES as _SYNTHETIC_RATER_2_SCORES,
)
from evals.injector import FailureSchedule
from evals.judge import JudgeVerdict, LlmJudge
from evals.judge_validation import compute_agreement
from evals.metrics import NOT_DEFINED, CaseResult, HeadlineMetrics, Metric, compute_headline_metrics
from evals.models import Case
from evals.profiles import load_case_profiles
from evals.repeated_runs import compute_variability, flipped_cases, unsafe_occurrences
from evals.report import EvaluationReport, SystemResult, Versions, render_markdown
from evals.runner.baselines.b0 import build_b0_app
from evals.runner.baselines.b1 import run_cases as run_b1_cases
from evals.runner.baselines.naive_agent_client import NaiveAgentClient
from evals.runner.runner import run_cases as run_http_cases
from evals.runner.smoke import smoke_cases
from pipelines.silver import git_version

logger = logging.getLogger(__name__)

_SYSTEMS = ("P", "B0", "B1")

#: Where the data pipeline writes its cleaned tables; the customer segment is read from there.
_SILVER_DIR = Path(__file__).resolve().parents[1] / "data" / "silver"

# The bank's operating zone (app.domain.calendar.BANK_ZONE): a fixed UTC-5 offset, stated here as
# the descriptive label the report's own text carries, since Bogotá has had no daylight-saving
# change since 1993.
_BANK_TIMEZONE_LABEL = "America/Bogota (UTC-5)"

# How many times each system runs for a full report: three for P (repeated runs expose run-to-run
# variability), one for a baseline (a single run has nothing to average or flip across).
_RUN_COUNTS = {"P": 3, "B0": 1, "B1": 1}

# The systems the live judge scores in a full report: P only, the scope the human judge validation
# also uses. B0 is verified structurally and needs no judge; B1 is a safety comparison baseline,
# not a system the judge's reliability is demonstrated against.
_JUDGED_SYSTEMS: frozenset[str] = frozenset({"P"})


def _select_cases(*, smoke: bool) -> tuple[Case, ...]:
    """The smoke subset when ``smoke`` is set, otherwise the whole golden set."""
    return smoke_cases() if smoke else ALL_CASES


def _require_test_login_key(settings: Settings) -> str:
    """The sandbox login's shared secret, already configured by the operator.

    Raises
    ------
    ConfigError
        The sandbox login is not enabled, or no key is set for it.
    """
    if not settings.test_identity_enabled or settings.test_identity_key is None:
        raise ConfigError(
            "the evaluation harness needs the sandbox login already enabled: set "
            "TEST_IDENTITY_ENABLED=true and TEST_IDENTITY_KEY before running P or B0"
        )
    return settings.test_identity_key.get_secret_value()


def _resolve_calendar(settings: Settings, *, clock: Clock) -> DomainCalendar:
    """The same domain-date resolution ``app.main`` applies when it builds a real application,
    for B1's own use — B1 never calls ``create_app``, so nothing else resolves this for it."""
    seed_date = None
    if not (settings.data_as_of_date or "").strip() and settings.database_url is not None:
        seed_date = read_data_as_of(settings.database_url.get_secret_value())
    try:
        return resolve_domain_calendar(settings.data_as_of_date, seed_date, now=clock)
    except DomainCalendarError as exc:
        raise ConfigError(str(exc)) from exc


def _run_p(
    settings: Settings, cases: Sequence[Case], *, capture_transcripts: bool = False
) -> tuple[CaseResult, ...]:
    """One run of the proposed system over ``cases``, with per-turn cost recorded.

    ``capture_transcripts`` keeps each case's reply text and facts so the live judge can score the
    run afterwards.
    """
    dsn = settings.require_database_url().get_secret_value()
    test_login_key = _require_test_login_key(settings)
    schedule = FailureSchedule()
    client = TestClient(create_app(settings, tool_port_decorator=schedule.decorate))
    with TurnCostLedger() as ledger:
        return run_http_cases(
            client,
            dsn,
            cases,
            test_login_key=test_login_key,
            capture_transcripts=capture_transcripts,
            cost_ledger=ledger,
            failure_schedule=schedule,
        )


def _run_b0(settings: Settings, cases: Sequence[Case]) -> tuple[CaseResult, ...]:
    """One run of baseline B0 (the deterministic pipeline behind a stubbed LLM) over ``cases``."""
    dsn = settings.require_database_url().get_secret_value()
    test_login_key = _require_test_login_key(settings)
    schedule = FailureSchedule()
    client = TestClient(build_b0_app(settings, tool_port_decorator=schedule.decorate))
    with TurnCostLedger() as ledger:
        return run_http_cases(
            client,
            dsn,
            cases,
            test_login_key=test_login_key,
            cost_ledger=ledger,
            failure_schedule=schedule,
        )


def _run_b1(settings: Settings, cases: Sequence[Case]) -> tuple[CaseResult, ...]:
    """One run of baseline B1 (the unsupervised tool-calling agent) over ``cases``.

    Calls the real model; it takes no transcript-capture option because the judge does not score B1.
    """
    dsn = settings.require_database_url().get_secret_value()
    calendar = _resolve_calendar(settings, clock=_real_clock)
    client = NaiveAgentClient(settings.require_anthropic_key(), model=settings.nlu_model)
    return run_b1_cases(
        client,
        settings,
        dsn,
        cases,
        policy=load_policy(),
        retriever=LexicalRetriever.from_corpus(),
        calendar=calendar,
        clock=_real_clock,
    )


# The batch runner of each system. Every runner takes ``(settings, cases)``; only P also accepts
# ``capture_transcripts``.
_RUNNERS: dict[str, Callable[..., tuple[CaseResult, ...]]] = {
    "P": _run_p,
    "B0": _run_b0,
    "B1": _run_b1,
}


def _score_with_judge(
    judge: LlmJudge, cases: Sequence[Case], results: Sequence[CaseResult]
) -> tuple[JudgeVerdict, ...]:
    """Score every case whose transcript was captured (``reply_text`` set) with the live judge.

    Results are matched to cases by ``case_id`` (the key ``evals.golden.h4_export.build_h4_rows``
    also uses) rather than by position. A case with no result, or none captured (see the
    Limitations of ``evals.runner.runner``), is skipped rather than scored against empty text,
    which the judge would score as "grounded" for having invented nothing and so misstate a gap as a
    pass.
    """
    results_by_case_id = {result.case_id: result for result in results}
    verdicts = []
    for case in cases:
        result = results_by_case_id.get(case.case_id)
        if result is None or result.reply_text is None or result.facts_and_sources is None:
            continue
        verdicts.append(
            judge.score(
                case.case_id,
                language=case.lang,
                user_turns=case.user_turns,
                system_replies=(result.reply_text,),
                facts_and_sources=result.facts_and_sources,
            )
        )
    return tuple(verdicts)


def _build_system_result(
    system: str,
    runs: Sequence[tuple[CaseResult, ...]],
    cases: Sequence[Case],
    judge: LlmJudge,
) -> SystemResult:
    """One system's ``SystemResult``, from its repeated (or single) runs' raw case results.

    The live judge scores only the last run of a system in ``_JUDGED_SYSTEMS`` (the run
    ``case_results`` keeps) and only the cases that run captured a transcript for; every other
    system reports an empty ``judge_verdicts``.
    """
    headline_runs = [compute_headline_metrics(run) for run in runs]
    judge_verdicts = _score_with_judge(judge, cases, runs[-1]) if system in _JUDGED_SYSTEMS else ()
    return SystemResult(
        system=system,  # type: ignore[arg-type]
        run_count=len(runs),
        variability=compute_variability(headline_runs),
        case_results=runs[-1],
        flips=flipped_cases(runs) if len(runs) > 1 else (),
        judge_verdicts=judge_verdicts,
        unsafe_occurrences=unsafe_occurrences(runs),
    )


def _run_full_report(settings: Settings, *, smoke: bool) -> tuple[EvaluationReport, bool]:
    """Run every system variant a full report covers, and assemble the report.

    P runs three times, B0 and B1 once each (``_RUN_COUNTS``). The agreement-with-human section
    reads the synthetic rater sample of ``evals.golden.judge_validation_sample`` and is labelled
    with its provenance; ``evals.h4_judge_validation`` patches the real sheets' result into the
    written report. It is a separate question from the judge-scored-quality section, which scores
    P's own last run directly with the live judge (``_JUDGED_SYSTEMS``).

    Parameters
    ----------
    smoke : bool
        Narrows the case set to ``evals.runner.smoke.smoke_cases()`` instead of the full golden
        set. The report's ``scope_note`` discloses the narrowing; the pipeline is otherwise
        unchanged.

    Returns
    -------
    tuple[EvaluationReport, bool]
        The report, and whether any case in any run (including a P run discarded from
        ``SystemResult.case_results``, which keeps only the last one) was unsafe — the exit-code
        enforcement needs every run checked, not only the one the report happens to display.
    """
    cases = _select_cases(smoke=smoke)
    calendar = _resolve_calendar(settings, clock=_real_clock)
    judge_client = CostTrackingLlm(AnthropicLlmClient(settings.require_anthropic_key()))
    judge = LlmJudge(judge_client, model=settings.judge_model)
    all_runs: dict[str, list[tuple[CaseResult, ...]]] = {}
    for system in _SYSTEMS:
        run_count = _RUN_COUNTS[system]
        runs = []
        for run_index in range(run_count):
            if system in _JUDGED_SYSTEMS and run_index == run_count - 1:
                runs.append(_RUNNERS[system](settings, cases, capture_transcripts=True))
            else:
                runs.append(_RUNNERS[system](settings, cases))
        all_runs[system] = runs
    unsafe = any(result.is_unsafe for runs in all_runs.values() for run in runs for result in run)
    for system, runs in all_runs.items():
        for run in runs:
            _log_errored_cases(system, run)
    systems = tuple(
        _build_system_result(system, all_runs[system], cases, judge) for system in _SYSTEMS
    )
    versions = Versions(
        nlu_model=settings.nlu_model,
        render_model=settings.render_model,
        judge_model=settings.judge_model,
        nlu_prompt_version=load_prompt("nlu_v1").version,
        render_prompt_version=load_prompt("render_v1").version,
        judge_prompt_version=load_prompt("judge_v1").version,
        policy_version=load_policy().version,
        git_sha=git_version(),
    )
    agreement = compute_agreement(
        _SYNTHETIC_RATER_1_SCORES, _SYNTHETIC_RATER_2_SCORES, _SYNTHETIC_JUDGE_VERDICTS
    )
    scope_note = (
        f"Generated from the {len(cases)}-case CI-smoke subset, not the full "
        f"{len(ALL_CASES)}-case golden set (including all 32 adversarial cases): --smoke was "
        "passed for a faster, narrower check. Re-run with --full alone for the full golden set."
        if smoke
        else ""
    )
    report = EvaluationReport(
        versions=versions,
        golden_cases=tuple(cases),
        case_profiles=_load_profiles(settings, cases),
        systems=systems,
        judge_validation=agreement,
        judge_validation_provenance=_SYNTHETIC_JUDGE_VALIDATION_PROVENANCE,
        judge_call_count=judge_client.call_count,
        judge_cost_usd=judge_client.total_cost_usd,
        reference_date=calendar.reference_date.isoformat(),
        reference_date_source=calendar.origin.value,
        bank_timezone=_BANK_TIMEZONE_LABEL,
        scope_note=scope_note,
    )
    return report, unsafe


def _load_profiles(settings: Settings, cases: Sequence[Case]) -> dict[str, CaseProfile] | None:
    """Each case's customer country and segment, or ``None`` when the lookup cannot run.

    The fairness section states that the profiles were unavailable instead of failing a run whose
    systems have already been scored.
    """
    try:
        return load_case_profiles(
            settings.require_database_url().get_secret_value(), cases, silver_dir=_SILVER_DIR
        )
    except (psycopg.Error, duckdb.Error, ConfigError):
        logger.exception("case_profiles_unavailable")
        return None


def _log_errored_cases(system: str, results: Sequence[CaseResult]) -> None:
    """Log every case in ``results`` that ``evals.scoring.error_result`` recorded, if any.

    Shared by both run modes: ``--system`` logs once per call through ``_log_report``; ``--full``
    logs once per system per run, because ``SystemResult.case_results`` keeps only the last run and
    an error in a discarded run would otherwise surface nowhere.
    """
    errored = [(result.case_id, result.error) for result in results if result.error is not None]
    if errored:
        logger.error(
            "errored_cases system=%s count=%d cases=%s",
            system,
            len(errored),
            ",".join(f"{case_id}({error})" for case_id, error in errored),
        )


def _fmt(metric: Metric) -> str:
    """A metric as ``value(n=denominator)`` for a log line, ``not_defined(n=...)`` if undefined."""
    if metric.value == NOT_DEFINED:
        return f"not_defined(n={metric.denominator})"
    return f"{metric.value:.3f}(n={metric.denominator})"


def _log_report(system: str, results: Sequence[CaseResult], metrics: HeadlineMetrics) -> None:
    """Log one system's headline metrics, cost and latency, and any unsafe or errored cases."""
    logger.info("evaluation_run system=%s cases=%d", system, len(results))
    logger.info(
        "headline_metrics safe_automated_resolution=%s attempted_share=%s "
        "conditional_automated_resolution=%s containment=%s escalation_quality=%s "
        "missed_transfers=%s unnecessary_transfers=%s unsafe_outcomes=%s",
        _fmt(metrics.safe_automated_resolution),
        _fmt(metrics.attempted_share),
        _fmt(metrics.conditional_automated_resolution),
        _fmt(metrics.containment),
        _fmt(metrics.escalation_quality),
        _fmt(metrics.transfers.missed),
        _fmt(metrics.transfers.unnecessary),
        _fmt(metrics.unsafe_outcomes),
    )
    logger.info(
        "latency_and_cost latency_p50=%s latency_p95=%s cost_per_attempted_case=%s "
        "cost_per_successful_automated_resolution=%s",
        _fmt(metrics.latency.p50),
        _fmt(metrics.latency.p95),
        _fmt(metrics.cost.per_attempted_case),
        _fmt(metrics.cost.per_successful_automated_resolution),
    )
    unsafe_case_ids = [result.case_id for result in results if result.is_unsafe]
    if unsafe_case_ids:
        logger.error(
            "unsafe_cases count=%d cases=%s", len(unsafe_case_ids), ",".join(unsafe_case_ids)
        )
    _log_errored_cases(system, results)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the evaluation harness; return the process exit code.

    Either ``--system {P,B0,B1} [--smoke]`` (one variant, one run, logged) or ``--full [--smoke]
    [--report PATH]`` (every variant, P three times, written to ``--report``). ``--full --smoke``
    narrows the full run to the smoke subset; the written report discloses that in its
    ``scope_note``. Returns ``1`` when any case was unsafe, otherwise ``0``.
    """
    parser = argparse.ArgumentParser(description="Run the evaluation harness.")
    parser.add_argument("--system", choices=_SYSTEMS)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Narrow the case set to the 16-case CI-smoke subset (--system or --full).",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run every system variant and write the full reports/evaluation.md.",
    )
    parser.add_argument("--report", type=Path, default=Path("reports/evaluation.md"))
    args = parser.parse_args(argv)
    if args.full == bool(args.system):
        parser.error("pass exactly one of --system or --full")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = load_settings()

    if args.full:
        report, unsafe = _run_full_report(settings, smoke=args.smoke)
        args.report.write_text(render_markdown(report), encoding="utf-8")
        logger.info("evaluation_report path=%s systems=%d", args.report, len(report.systems))
        return 1 if unsafe else 0

    cases = _select_cases(smoke=args.smoke)
    results = _RUNNERS[args.system](settings, cases)
    metrics = compute_headline_metrics(results)
    _log_report(args.system, results, metrics)

    return 1 if any(result.is_unsafe for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
