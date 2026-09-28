"""
Evaluation CLI
================

Overview
--------
``make evaluate SYSTEM={P|B0|B1}``'s implementation: runs one system variant against a set of
golden-set cases and logs the resulting headline metrics. ``--smoke`` narrows the case set to
``evals.runner.smoke.smoke_cases()`` (the CI-gating slice, "all injection + authz"); its absence
runs the full golden set (``evals.golden.case_sheet.ALL_CASES``).

Scope
-----
In: choosing and building the right dependencies for the requested system variant, running the
batch, logging a summary, and the process exit code the CI smoke job gates merge on.
Out: the full evaluation report (``reports/evaluation.md``; the judge, three repeated runs and the
report generator are a later slice's own job, per ``plan/delivery/streams.md``); loading any seed
data into the target store — the caller's own responsibility (``make load-seed`` for a real run
against ``data/gold/ops_seed``, a CI-only fixture for the smoke job).

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

Runtime Contract
-----------------
``main(argv) -> int``. Command line: ``python -m evals.cli --system {P,B0,B1} [--smoke]``.

Limitations
-----------
Logs ``HeadlineMetrics`` as structured lines; no ``reports/evaluation.md`` generator, no judge, no
repeated-run averaging. A case the scorer does not yet cover, or a case runtime error (a non-2xx
response, a malformed ``seed_ref``), still raises and aborts the whole batch — the already-
reviewed ``run_cases`` functions' own "no hidden retry" rule, unchanged here.
"""

from __future__ import annotations

# Standard libraries
import argparse
import logging
from collections.abc import Sequence

# Third-party libraries
from starlette.testclient import TestClient

# Local modules
from app.config import ConfigError, Settings, load_settings
from app.domain.calendar import DomainCalendar, DomainCalendarError, resolve_domain_calendar
from app.domain.policy.loader import load_policy
from app.main import create_app
from app.persistence.ops_meta import read_data_as_of
from app.retrieval.lexical import LexicalRetriever
from app.security.sessions import Clock
from app.security.sessions import utc_now as _real_clock
from evals.golden.case_sheet import ALL_CASES
from evals.metrics import NOT_DEFINED, CaseResult, HeadlineMetrics, Metric, compute_headline_metrics
from evals.models import Case
from evals.runner.baselines.b0 import build_b0_app
from evals.runner.baselines.b1 import run_cases as run_b1_cases
from evals.runner.baselines.naive_agent_client import NaiveAgentClient
from evals.runner.runner import run_cases as run_http_cases
from evals.runner.smoke import smoke_cases

logger = logging.getLogger(__name__)

_SYSTEMS = ("P", "B0", "B1")


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


def _run_p(settings: Settings, cases: Sequence[Case]) -> tuple[CaseResult, ...]:
    dsn = settings.require_database_url().get_secret_value()
    test_login_key = _require_test_login_key(settings)
    client = TestClient(create_app(settings))
    return run_http_cases(client, dsn, cases, test_login_key=test_login_key)


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


_RUNNERS = {"P": _run_p, "B0": _run_b0, "B1": _run_b1}


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


def main(argv: Sequence[str] | None = None) -> int:
    """Run one system variant against a set of golden-set cases; return the process exit code."""
    parser = argparse.ArgumentParser(description="Run the evaluation harness against one system.")
    parser.add_argument("--system", choices=_SYSTEMS, required=True)
    parser.add_argument("--smoke", action="store_true", help="Run the CI-gating smoke slice only.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = load_settings()
    cases = _select_cases(smoke=args.smoke)
    results = _RUNNERS[args.system](settings, cases)
    metrics = compute_headline_metrics(results)
    _log_report(args.system, results, metrics)

    return 1 if any(result.is_unsafe for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
