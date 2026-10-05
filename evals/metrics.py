"""
Evaluation Metrics Engine
=========================

Overview
--------
Implements the headline metric formulas, including the "not defined" reporting rule: pure
functions from a harness run's per-case verdicts (``CaseResult``, produced by the runner) to the
headline metrics of the report.

Scope
-----
In: the formulas for safe automated resolution, containment, escalation quality, unsafe
outcomes and operating efficiency, each labeled with its ``basis`` (every metric this module
computes is ``measured``; a projected metric, such as the SLA-breach projection, is computed
elsewhere from the workflow analysis and is never produced here).
Out: running a system variant against the golden set (the runner), the LLM judge, fairness slicing
by language/country/segment (``evals.fairness``) and the learned-component metrics (PR-AUC,
calibration; those score a model, not a conversation, and live beside the model that produces them).

Design Principles
-----------------
- **Pure functions over plain records.** ``CaseResult`` carries only the flags a formula needs;
  nothing here reads a database, a log or the network, so a formula's tests are exact and fast.
- **The "not defined" rule is a value, not an exception.** Cost per successful automated
  resolution has no meaning when a variant produced zero successes; the metric's value is then
  the literal ``"not defined"``, never a division by zero and never a fabricated number such as
  0 or infinity.
- **Every metric states its denominator.** A rate alone can be misread as more evidence than it
  is; every ratio in ``HeadlineMetrics`` carries the count and the size of the set it was
  computed over, so a report reader (or a test) can see how small a sample is.
- **Adversarial cases are excluded from the correctness-rate sets (S, A, E) by default**, but
  count fully toward unsafe outcomes: that is what they exist to measure.

Runtime Contract
----------------
``compute_headline_metrics(results) -> HeadlineMetrics``: pure, deterministic, no I/O.

Limitations
-----------
``CaseResult`` reports one verdict per case: unsafe outcomes and latency have no per-category
breakdown, and latency has no per-turn granularity. A caller that needs either slices the
``results`` sequence itself before calling this module (or, for language/country/segment slicing,
uses ``evals.fairness``) rather than this module inferring categories it is not given. This module
does nothing special for a case the runner could not resolve, run or score (``CaseResult.error``
set): its safe-default fields count it as attempted nowhere and correct nowhere, so it lowers every
rate's numerator without inflating any denominator's meaning — naming and surfacing *which* cases
errored, and why, is the runner's and the report's own job, not this pure-function engine's.
``reply_text`` and ``facts_and_sources`` are opaque to every formula here: they exist for a judge or
a human rater to read, not for this module to score, and ``compute_headline_metrics`` never inspects
either.
"""

from __future__ import annotations

# Standard libraries
import math  # Percentile index rounding
from collections.abc import Sequence  # Type of a run's per-case verdicts
from dataclasses import dataclass  # Immutable result objects
from typing import Literal  # The "not defined" sentinel

# -----------------------------------------------------------------------------
# Constants and types
# -----------------------------------------------------------------------------

#: Reported when a ratio's denominator is a set the run defines as empty by construction, or
#: when cost per successful automated resolution has no successes to divide by.
NOT_DEFINED: Literal["not defined"] = "not defined"

#: How a metric's value was produced. Every metric this engine computes is ``measured``; a
#: ``projected`` metric is computed elsewhere, from the workflow analysis, and carries this
#: label so the report never confuses the two.
Basis = Literal["measured", "projected"]

#: A metric's value: a number, or ``NOT_DEFINED``.
MetricValue = float | Literal["not defined"]


@dataclass(frozen=True, slots=True)
class Metric:
    """One reported number, always labeled by how it was produced and over how large a set.

    ``value`` is a float or ``NOT_DEFINED``; ``denominator`` is the size of the set it was computed
    over (zero when ``value`` is ``NOT_DEFINED`` because that set was empty).
    """

    value: MetricValue
    basis: Basis
    denominator: int


@dataclass(frozen=True, slots=True)
class CaseResult:
    """The deterministic verdict of one executed golden-set case, or a named record of why one
    could not be produced.

    Produced by the runner after replaying a case against a system variant and applying the
    deterministic checks of ``evals.scoring``; the LLM judge's rubric scores are reported
    separately and are not inputs to this engine. When ``error`` is set, the case could not be
    resolved, run or scored — every other field holds its safe default (an unattempted, incorrect,
    non-escalating, non-adversarial-by-record outcome, per ``is_adversarial``'s own value which is
    always knowable from the case itself), so this result still folds into every metric's
    denominator without needing a separate code path, and cannot be mistaken for a real success.
    """

    case_id: str
    is_adversarial: bool
    expected_escalation: bool
    observed_escalation: bool
    automation_attempted: bool
    correct_outcome: bool
    useful_handoff_packet: bool = False
    automated_success: bool = False
    is_unsafe: bool = False
    unsafe_reasons: tuple[Literal["pii_leaked", "unbacked_handoff"], ...] = ()
    """Which of the harness's own unsafe checks fired; empty when ``is_unsafe`` is ``False``. Both
    can fire together for the same case, so this is a tuple, never a single value forced to pick
    one. Never derived from ``is_unsafe`` — the two are always set together by the same caller."""
    latency_seconds: float | None = None
    cost_usd: float | None = None
    error: str | None = None
    reply_text: str | None = None
    """Every reply the run produced, joined in order and PAN-masked. ``None`` unless the caller
    opted into capture (``evals.runner.runner.run_cases``'s own ``capture_transcripts`` flag) —
    every other run leaves this unset."""
    facts_and_sources: str | None = None
    """The grounding text (``evals.facts.assemble_facts_and_sources``) the reply is checked
    against, PAN-masked. Set together with ``reply_text``, by the same opt-in capture step, never
    independently."""


@dataclass(frozen=True, slots=True)
class TransferCounts:
    """Escalations the system got wrong, each with the set it is a share of."""

    missed: Metric
    """Should-escalate cases the system resolved automatically instead (share of E)."""
    unnecessary: Metric
    """Should-automate cases the system escalated instead (share of S minus E)."""


@dataclass(frozen=True, slots=True)
class LatencyMetrics:
    """End-to-end latency, in seconds, over in-scope cases that reported a duration."""

    p50: Metric
    p95: Metric


@dataclass(frozen=True, slots=True)
class CostMetrics:
    """Model cost, in US dollars, averaged over in-scope cases that reported one."""

    per_attempted_case: Metric
    """Mean cost of the attempted cases that reported a cost."""
    per_successful_automated_resolution: Metric
    """Mean cost of the automated successes that reported a cost; ``NOT_DEFINED`` with none."""


@dataclass(frozen=True, slots=True)
class HeadlineMetrics:
    """Every headline metric measured for one system variant's run.

    Over the sets: S is the in-scope (non-adversarial) cases, A the subset of S where automation
    was attempted, and E the subset of S whose correct handling is an escalation to a person.
    """

    safe_automated_resolution: Metric
    """|correct, policy-compliant outcome with no human| / |S|."""
    attempted_share: Metric
    """|A| / |S|."""
    conditional_automated_resolution: Metric
    """Safe automated resolution's rate over A alone, not S."""
    containment: Metric
    """|cases ended without transfer| / |S|."""
    escalation_quality: Metric
    """Correct transfers with a useful packet / |E|."""
    transfers: TransferCounts
    unsafe_outcomes: Metric
    """Count of unauthorized disclosures/actions or materially incorrect outcomes / |all cases|,
    adversarial included: zero observed in a small set does not establish zero risk, and the
    denominator states exactly how small."""
    latency: LatencyMetrics
    cost: CostMetrics


# -----------------------------------------------------------------------------
# Formulas
# -----------------------------------------------------------------------------


def _rate(numerator: int, denominator: int) -> Metric:
    """A share as a measured metric; ``not defined`` only when the set itself is empty."""
    if denominator == 0:
        return Metric(NOT_DEFINED, basis="measured", denominator=0)
    return Metric(numerator / denominator, basis="measured", denominator=denominator)


def _is_safe_automated_resolution(result: CaseResult) -> bool:
    """A case resolved without escalation and with the correct outcome."""
    return not result.observed_escalation and result.correct_outcome


def _percentile(values: Sequence[float], fraction: float) -> float:
    """The value at ``fraction`` through the sorted sample, no interpolation between neighbors.

    A fixed, dependency-free rule: the index is ``ceil(fraction * n) - 1``, clamped to the last
    element, so p50 of an even-sized sample is its lower median and p100 is always the maximum.
    """
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def compute_headline_metrics(results: Sequence[CaseResult]) -> HeadlineMetrics:
    """Score one system variant's run against the golden set.

    Parameters
    ----------
    results
        One ``CaseResult`` per case the variant was run against, adversarial cases included.

    Returns
    -------
    HeadlineMetrics
        Every headline metric, each labeled ``measured`` with its own denominator.
    """
    in_scope = [r for r in results if not r.is_adversarial]
    scope_size = len(in_scope)
    escalation_expected = [r for r in in_scope if r.expected_escalation]
    escalation_size = len(escalation_expected)
    attempted = [r for r in in_scope if r.automation_attempted]
    attempted_size = len(attempted)

    safe_automated = sum(1 for r in in_scope if _is_safe_automated_resolution(r))
    safe_automated_over_attempted = sum(1 for r in attempted if _is_safe_automated_resolution(r))
    contained = sum(1 for r in in_scope if not r.observed_escalation)
    correct_transfers = sum(
        1 for r in escalation_expected if r.observed_escalation and r.useful_handoff_packet
    )
    missed = sum(1 for r in escalation_expected if not r.observed_escalation)
    non_escalation_expected_size = scope_size - escalation_size
    unnecessary = sum(1 for r in in_scope if not r.expected_escalation and r.observed_escalation)

    unsafe_denominator = len(results)
    unsafe = sum(1 for r in results if r.is_unsafe)

    latencies = [r.latency_seconds for r in in_scope if r.latency_seconds is not None]
    costs_attempted = [r.cost_usd for r in attempted if r.cost_usd is not None]
    successful = [r for r in in_scope if r.automated_success]
    success_costs = [r.cost_usd for r in successful if r.cost_usd is not None]

    if latencies:
        p50 = Metric(_percentile(latencies, 0.50), basis="measured", denominator=len(latencies))
        p95 = Metric(_percentile(latencies, 0.95), basis="measured", denominator=len(latencies))
    else:
        p50 = Metric(NOT_DEFINED, basis="measured", denominator=0)
        p95 = Metric(NOT_DEFINED, basis="measured", denominator=0)

    if costs_attempted:
        cost_per_attempted = Metric(
            sum(costs_attempted) / len(costs_attempted),
            basis="measured",
            denominator=len(costs_attempted),
        )
    else:
        cost_per_attempted = Metric(NOT_DEFINED, basis="measured", denominator=0)

    if success_costs:
        cost_per_success = Metric(
            sum(success_costs) / len(success_costs),
            basis="measured",
            denominator=len(success_costs),
        )
    else:
        # The "not defined" rule: no cost data among successes means no meaningful average.
        cost_per_success = Metric(NOT_DEFINED, basis="measured", denominator=len(success_costs))

    return HeadlineMetrics(
        safe_automated_resolution=_rate(safe_automated, scope_size),
        attempted_share=_rate(attempted_size, scope_size),
        conditional_automated_resolution=_rate(safe_automated_over_attempted, attempted_size),
        containment=_rate(contained, scope_size),
        escalation_quality=_rate(correct_transfers, escalation_size),
        transfers=TransferCounts(
            missed=_rate(missed, escalation_size),
            unnecessary=_rate(unnecessary, non_escalation_expected_size),
        ),
        unsafe_outcomes=_rate(unsafe, unsafe_denominator),
        latency=LatencyMetrics(p50=p50, p95=p95),
        cost=CostMetrics(
            per_attempted_case=cost_per_attempted,
            per_successful_automated_resolution=cost_per_success,
        ),
    )
