"""
Repeated-Run Variability
==========================

Overview
--------
``plan/docs/evaluation-plan.md``'s Execution protocol runs the proposed system (P) three times
and reports "mean +/- range on headline metrics" plus "a per-case flip list"; B0 and B1 run once
and have no variability to report. This module computes both, as a pure function of the
``HeadlineMetrics``/``CaseResult`` sequences the runner (a prior slice) already produces —
nothing here re-runs a case or calls a system variant.

Scope
-----
In: mean/low/high across repeated ``HeadlineMetrics`` runs, one ``MetricVariability`` per headline
metric; the set of case ids whose ``correct_outcome`` or ``is_unsafe`` differs across the repeated
runs.
Out: running P three times (the CLI's job); anything about a system variant that runs only once
(B0, B1 report no variability, by construction — this module is never called for them).

Design Principles
-----------------
- **"Not defined" propagates, never averages away.** A metric that is "not defined" in even one
  repeated run (for example, a run with zero successful automated resolutions) makes that metric's
  variability "not defined" too, the same reporting rule ``evals.metrics`` already applies:
  silently averaging over only the runs that happened to have a value would understate how
  unstable the metric actually is.
- **A flip is defined at the verdict level, not the metric level.** Two runs can produce the same
  headline numbers while disagreeing about which specific cases passed — the flip list exists
  because the mean/range alone would hide exactly that; the two are reported side by side, not
  merged into one summary.
- **A case missing from one run is itself a flip.** A case a later run does not report a result
  for (a run that crashed partway, or a case set that changed between runs) counts as a flip,
  never as silently equal to whatever the other runs said.

Runtime Contract
-----------------
``MetricVariability(mean, low, high)``.
``HeadlineMetricsVariability`` — one ``MetricVariability`` per ``HeadlineMetrics`` field.
``compute_variability(runs) -> HeadlineMetricsVariability`` raises ``ValueError`` if ``runs`` is
empty.
``CaseFlip(case_id, correct_outcome_by_run, is_unsafe_by_run)``.
``flipped_cases(runs) -> tuple[CaseFlip, ...]``, one entry per case id where either flag differs
across the runs it appears in, or the case is missing from at least one run.
``UnsafeOccurrence(run_index, result)``.
``unsafe_occurrences(runs) -> tuple[UnsafeOccurrence, ...]``, every unsafe ``CaseResult`` across
``runs``, tagged with its run's index, in run order.

Limitations
-----------
"Missing from a run" means the runner produced no ``CaseResult`` at all for that case id in that
run — for example a run the CLI's own "no hidden retry" rule aborted partway through. Each
``CaseFlip`` entry's ``correct_outcome_by_run``/``is_unsafe_by_run`` carries ``None`` at that run's
position rather than guessing a value, so a report reader can tell "flipped" apart from "never
ran" at a glance.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

# Local modules
from evals.metrics import NOT_DEFINED, CaseResult, HeadlineMetrics, MetricValue

VariabilityValue = float | Literal["not defined"]


@dataclass(frozen=True, slots=True)
class MetricVariability:
    """Mean, low and high of one metric across the repeated runs it was measured in."""

    mean: VariabilityValue
    low: VariabilityValue
    high: VariabilityValue


@dataclass(frozen=True, slots=True)
class HeadlineMetricsVariability:
    """One ``MetricVariability`` per field of ``evals.metrics.HeadlineMetrics``."""

    safe_automated_resolution: MetricVariability
    attempted_share: MetricVariability
    conditional_automated_resolution: MetricVariability
    containment: MetricVariability
    escalation_quality: MetricVariability
    missed_transfers: MetricVariability
    unnecessary_transfers: MetricVariability
    unsafe_outcomes: MetricVariability
    latency_p50: MetricVariability
    latency_p95: MetricVariability
    cost_per_attempted_case: MetricVariability
    cost_per_successful_automated_resolution: MetricVariability


def _variability(values: Sequence[MetricValue]) -> MetricVariability:
    defined = [value for value in values if value != NOT_DEFINED]
    if len(defined) != len(values):
        return MetricVariability(NOT_DEFINED, NOT_DEFINED, NOT_DEFINED)
    numbers = [value for value in defined if isinstance(value, float)]
    return MetricVariability(mean=sum(numbers) / len(numbers), low=min(numbers), high=max(numbers))


def compute_variability(runs: Sequence[HeadlineMetrics]) -> HeadlineMetricsVariability:
    """Mean/low/high across ``runs``' repeated measurements of the same headline metrics.

    Raises
    ------
    ValueError
        ``runs`` is empty; there is nothing to compute variability over.
    """
    if not runs:
        raise ValueError("compute_variability needs at least one run")
    return HeadlineMetricsVariability(
        safe_automated_resolution=_variability(
            [run.safe_automated_resolution.value for run in runs]
        ),
        attempted_share=_variability([run.attempted_share.value for run in runs]),
        conditional_automated_resolution=_variability(
            [run.conditional_automated_resolution.value for run in runs]
        ),
        containment=_variability([run.containment.value for run in runs]),
        escalation_quality=_variability([run.escalation_quality.value for run in runs]),
        missed_transfers=_variability([run.transfers.missed.value for run in runs]),
        unnecessary_transfers=_variability([run.transfers.unnecessary.value for run in runs]),
        unsafe_outcomes=_variability([run.unsafe_outcomes.value for run in runs]),
        latency_p50=_variability([run.latency.p50.value for run in runs]),
        latency_p95=_variability([run.latency.p95.value for run in runs]),
        cost_per_attempted_case=_variability([run.cost.per_attempted_case.value for run in runs]),
        cost_per_successful_automated_resolution=_variability(
            [run.cost.per_successful_automated_resolution.value for run in runs]
        ),
    )


@dataclass(frozen=True, slots=True)
class CaseFlip:
    """One case whose deterministic verdict was not identical across the repeated runs."""

    case_id: str
    correct_outcome_by_run: tuple[bool | None, ...]
    """``None`` at a run's position means the case was missing from that run entirely."""
    is_unsafe_by_run: tuple[bool | None, ...]


@dataclass(frozen=True, slots=True)
class UnsafeOccurrence:
    """One unsafe ``CaseResult``, tagged with which repeated run produced it.

    ``SystemResult.case_results`` keeps only the last run, so a run-1 unsafe verdict a later run
    did not repeat would otherwise be unrecoverable from the report; this is every unsafe result
    from every run, not only the last.
    """

    run_index: int
    """0-based position of the run this result came from, among ``runs`` as given to
    ``unsafe_occurrences``."""
    result: CaseResult


def unsafe_occurrences(runs: Sequence[Sequence[CaseResult]]) -> tuple[UnsafeOccurrence, ...]:
    """Every unsafe ``CaseResult`` across ``runs``, in run order, run 0 first.

    The same case can appear more than once if it was unsafe in more than one run — each
    occurrence is its own entry, never deduplicated by case id, since a report reader needs to
    know it happened twice, not just that it happened.
    """
    return tuple(
        UnsafeOccurrence(run_index=run_index, result=result)
        for run_index, run in enumerate(runs)
        for result in run
        if result.is_unsafe
    )


def flipped_cases(runs: Sequence[Sequence[CaseResult]]) -> tuple[CaseFlip, ...]:
    """Cases whose ``correct_outcome`` or ``is_unsafe`` differs across ``runs``, or that are
    missing from at least one run (see this module's Limitations for what "missing" means here).
    """
    all_ids: dict[str, None] = {}
    for run in runs:
        for result in run:
            all_ids.setdefault(result.case_id, None)

    flips = []
    for case_id in all_ids:
        by_run: list[CaseResult | None] = []
        for run in runs:
            match = next((r for r in run if r.case_id == case_id), None)
            by_run.append(match)
        correct = tuple(r.correct_outcome if r is not None else None for r in by_run)
        unsafe = tuple(r.is_unsafe if r is not None else None for r in by_run)
        if len(set(correct)) > 1 or len(set(unsafe)) > 1:
            flips.append(
                CaseFlip(case_id=case_id, correct_outcome_by_run=correct, is_unsafe_by_run=unsafe)
            )
    return tuple(flips)
