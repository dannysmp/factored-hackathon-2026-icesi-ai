"""
Evaluation CLI
================

Overview
--------
Two modes, both accepting ``--smoke`` to narrow the case set to
``evals.runner.smoke.smoke_cases()`` (16 cases) instead of the full golden set,
``evals.golden.case_sheet.ALL_CASES`` (135 cases, including all 32 adversarial cases).
``make evaluate SYSTEM={P|B0|B1} [SMOKE=1]``: runs one system variant once and logs the resulting
headline metrics (unchanged from every earlier increment, including the CI-gating smoke job).
``make evaluate FULL=1 [SMOKE=1]``: runs every system variant (P three times, B0 and B1 once each,
the plan's own execution protocol) and writes the full ``reports/evaluation.md`` — the first
increment able to produce the plan's single generated report artifact end to end. The full
135-case golden set (every ``expected_intent`` it declares, including all 32 adversarial cases)
runs either way; ``SMOKE=1`` stays available as a deliberately narrower, faster scope for a quick
check, and the written report's own ``scope_note`` discloses the narrowing whenever it is used,
rather than silently under-reporting. ``--full`` additionally scores P's last run with the live
judge (``_JUDGED_SYSTEMS``), feeding the report's own judge-scored-quality section — B0 and B1
carry no judge verdicts, for the same reason H4's own human validation is scoped to the proposed
system alone.

Scope
-----
In: choosing and building the right dependencies for the requested system variant(s), running the
batch(es), logging a summary or writing the full report, scoring P's last ``--full`` run with the
live judge, and the process exit code the CI smoke job (and, for ``--full``, any run of any
variant) gates on.
Out: loading any seed data into the target store — the caller's own responsibility (``make
load-seed`` for a real run against ``data/gold/ops_seed``, followed by ``make load-eval-bank`` for
the full 32-case adversarial set (the 16-case CI-smoke subset needs only ``ops_seed``), a CI-only
fixture for the smoke job);
the judge-validation (agreement-with-human) section, which still reads the H4 sample separately
(synthetic today, the real returned sheets later; see ``evals.judge_validation``) — a live judge
call over P's own run answers "how good is this run," not "how well does the judge agree with a
human," which is a different question this module leaves alone.

Design Principles
-----------------
- **P and B0 drive over the real HTTP surface, in process.** Both are built with
  ``app.main.create_app`` (B0 via ``evals.runner.baselines.b0.build_b0_app``, which additionally
  forces a stubbed LLM) and driven through ``starlette.testclient.TestClient``, the same shape
  every already-reviewed integration test in this slice already uses — never a real network
  socket, never a second, unreviewed HTTP client construction path.
- **B1 never touches the HTTP surface at all.** Its own batch runner
  (``evals.runner.baselines.b1.run_cases``) builds a real ``NaiveAgentClient`` against a real
  Anthropic key; there is no stub path for B1's own model call, since B1 exists specifically to
  measure what an unsupervised model does. A stubbed B1 run is a contradiction in terms, not a
  cheaper smoke test of one — which is also why B1 never runs in the PR-gating CI smoke job.
- **The sandbox login must already be enabled; this module never turns it on.** P and B0 both
  authenticate through ``POST /v1/auth/test-sessions``, which only exists when
  ``settings.test_identity_enabled`` is already true. Forcing it on here — the way
  ``build_b0_app`` forces ``llm_provider`` — would mean a CLI invocation could silently expose a
  public, no-password login path an operator's own configuration never asked for; this module
  fails loudly instead, naming the two settings to set.
- **The model id B1 calls with is ``settings.nlu_model``.** The evaluation plan's "same model"
  wording does not distinguish, for a single unified agent role, between P's two configured
  models (understanding vs. rendering); B1's defining behavior is deciding which tool to call
  next, the closer analogue of the understanding role, so this module names that one. Disclosed
  here, not hidden, in case a later increment judges differently — see
  ``evals.runner.baselines.b1``'s own Limitations, which names this exact open question.
- **The exit code is the enforcement mechanism, not a separate check.** A batch with any
  ``CaseResult.is_unsafe`` exits ``1`` — the literal mechanism behind "a regression that turns any
  adversarial case unsafe blocks merge" (``plan/docs/evaluation-plan.md``'s CI wiring section).
- **A judge call that cannot complete aborts ``--full``, not just that one case.** ``evals.judge``
  already documents this as deliberate ("a judge call has no customer waiting on it: any
  ``LlmError`` propagates to the caller"); this module does not add a swallow-and-continue around
  it that the judge's own module explicitly chose not to have.

Runtime Contract
-----------------
``main(argv) -> int``. Command line: ``python -m evals.cli --system {P,B0,B1} [--smoke]`` or
``python -m evals.cli --full [--smoke] [--report PATH]`` (default ``reports/evaluation.md``);
exactly one of ``--system``/``--full`` is required, and ``--smoke`` applies to either.

Limitations
-----------
A case that fails to resolve, run or score with one of ``evals.runner.runner``'s or
``evals.runner.baselines.b1``'s own documented failure classes no longer aborts the batch; it is
recorded as a named ``CaseResult.error`` and the batch continues, on both ``--system`` and
``--full`` (each of ``--full``'s several batches applies this independently, one system at a time).
An unanticipated exception outside those documented classes still propagates and stops the run.
``--full``'s judge-validation (agreement-with-human) section is only as real as its own data source
(see Scope); it is not itself run per system per call. The live judge scores P's last run only, and
only the cases that run actually captured a transcript for (``evals.runner.runner``'s own
Limitations (capture)) — a case capture missed is silently absent from the judge-scored-quality
section's own denominator, not reported as a zero.
"""

from __future__ import annotations

# Standard libraries
import argparse
import logging
from collections.abc import Callable, Sequence
from pathlib import Path

# Third-party libraries
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
from evals.judge import JudgeVerdict, LlmJudge
from evals.judge_validation import compute_agreement
from evals.metrics import NOT_DEFINED, CaseResult, HeadlineMetrics, Metric, compute_headline_metrics
from evals.models import Case
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

# The bank's operating zone (app.domain.calendar.BANK_ZONE): a fixed UTC-5 offset, stated here as
# the descriptive label the report's own text carries, since Bogotá has had no daylight-saving
# change since 1993.
_BANK_TIMEZONE_LABEL = "America/Bogota (UTC-5)"

# How many times each system runs for a full report: 3 for P (the plan's own repeated-run
# requirement), 1 for a baseline (there is nothing to average or flip across a single run).
_RUN_COUNTS = {"P": 3, "B0": 1, "B1": 1}

# The systems the live judge scores in a full report: P only, the same scope H4's own human
# validation uses (B0 is structurally verified already and needs no judge to trust; B1 is a
# safety comparison baseline, not a system the judge's reliability is demonstrated against) — the
# same reasoning applies to judge-sourced report metrics generally, not only to human validation.
_JUDGED_SYSTEMS: frozenset[str] = frozenset({"P"})


def _select_cases(*, smoke: bool) -> tuple[Case, ...]:
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
    dsn = settings.require_database_url().get_secret_value()
    test_login_key = _require_test_login_key(settings)
    client = TestClient(create_app(settings))
    return run_http_cases(
        client, dsn, cases, test_login_key=test_login_key, capture_transcripts=capture_transcripts
    )


def _run_b0(settings: Settings, cases: Sequence[Case]) -> tuple[CaseResult, ...]:
    dsn = settings.require_database_url().get_secret_value()
    test_login_key = _require_test_login_key(settings)
    client = TestClient(build_b0_app(settings))
    return run_http_cases(client, dsn, cases, test_login_key=test_login_key)


def _run_b1(settings: Settings, cases: Sequence[Case]) -> tuple[CaseResult, ...]:
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


_RUNNERS: dict[str, Callable[..., tuple[CaseResult, ...]]] = {
    "P": _run_p,
    "B0": _run_b0,
    "B1": _run_b1,
}


def _score_with_judge(
    judge: LlmJudge, cases: Sequence[Case], results: Sequence[CaseResult]
) -> tuple[JudgeVerdict, ...]:
    """Score every case whose transcript was captured (``reply_text`` set) with the live judge.

    Matched by ``case_id``, the same key ``evals.golden.h4_export.build_h4_rows`` already matches
    a captured result against, rather than assuming ``cases`` and ``results`` share one order and
    length — a case ``results`` carries nothing for (or nothing captured, matching
    ``evals.runner.runner``'s own Limitations (capture): a declared policy section that failed to
    resolve) is skipped here, not scored against empty text, which would let a judge call
    trivially score "grounded" for having invented nothing, misstating a gap as a pass.
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

    The live judge scores only the last run of a system in ``_JUDGED_SYSTEMS`` (matching
    ``case_results``' own "last run only" convention) and only the cases that run captured a
    transcript for; every other system reports an empty ``judge_verdicts``, exactly as before this
    capability existed.
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
    """Run every system variant the plan's execution protocol calls for, and assemble the report.

    P runs three times, B0 and B1 once each (``_RUN_COUNTS``); the judge-validation
    (agreement-with-human) section reads this slice's own synthetic placeholder sample until the
    real H4 sheets replace it (see ``evals.golden.judge_validation_sample``) — a separate question
    from the judge-scored-quality section, which scores P's own last run directly with the live
    judge (``_JUDGED_SYSTEMS``).

    Parameters
    ----------
    smoke : bool
        Narrows the case set to ``evals.runner.smoke.smoke_cases()`` (16 cases, the CI-gating
        subset) instead of the full golden set (135 cases, including all 32 adversarial cases).
        The full set is fully runnable today (``app.persistence.load_eval_bank`` resolves every
        adversarial case's data, and ``evals.scoring.score_case`` covers every outcome class the
        golden set declares); ``--smoke`` stays available as a deliberately narrower, faster scope
        for a quick check, not a fallback for a missing dependency. The resulting report's own
        ``scope_note`` discloses the narrowing whenever it is used; nothing about the pipeline
        itself changes.

    Returns
    -------
    tuple[EvaluationReport, bool]
        The report, and whether any case in any run (including a P run discarded from
        ``SystemResult.case_results``, which keeps only the last one) was unsafe — the exit-code
        enforcement needs every run checked, not only the one the report happens to display.
    """
    cases = _select_cases(smoke=smoke)
    calendar = _resolve_calendar(settings, clock=_real_clock)
    judge = LlmJudge(
        AnthropicLlmClient(settings.require_anthropic_key()), model=settings.judge_model
    )
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
        systems=systems,
        judge_validation=agreement,
        judge_validation_provenance=_SYNTHETIC_JUDGE_VALIDATION_PROVENANCE,
        reference_date=calendar.reference_date.isoformat(),
        reference_date_source=calendar.origin.value,
        bank_timezone=_BANK_TIMEZONE_LABEL,
        scope_note=scope_note,
    )
    return report, unsafe


def _log_errored_cases(system: str, results: Sequence[CaseResult]) -> None:
    """Log every case in ``results`` that ``evals.scoring.error_result`` recorded, if any.

    Shared by both run modes: ``--system`` logs it once per call via ``_log_report``; ``--full``
    logs it once per system per run, since ``SystemResult.case_results`` keeps only the last run
    and an error in a discarded run would otherwise never surface anywhere.
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
    if metric.value == NOT_DEFINED:
        return f"not_defined(n={metric.denominator})"
    return f"{metric.value:.3f}(n={metric.denominator})"


def _log_report(system: str, results: Sequence[CaseResult], metrics: HeadlineMetrics) -> None:
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

    Either ``--system {P,B0,B1} [--smoke]`` (one variant, one run, logged as before — unchanged
    from every earlier increment, including the CI-gating smoke job) or ``--full [--smoke]
    [--report PATH]`` (every variant, P three times, written as ``reports/evaluation.md``).
    ``--full --smoke`` narrows the full run to the 16-case CI-smoke subset too, for a faster check;
    the written report discloses the narrowing in its own ``scope_note``.
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
