"""
Evaluation Metrics Engine Tests
================================

Component: ``evals.metrics``. Hermetic: every case result is built in memory, so a test pins the
exact formula rather than a real run's numbers. Each set boundary (S, A, E, adversarial) and the
"not defined" rule get their own isolated case, so removing any one condition from the engine
fails a specific test.
"""

from __future__ import annotations

# Standard libraries
from typing import Any

# Third-party libraries
import pytest

# Local modules
from evals.metrics import (
    NOT_DEFINED,
    CaseResult,
    compute_headline_metrics,
)


def _result(**overrides: Any) -> CaseResult:
    """A safely, correctly automated in-scope case; overrides narrow it to what a test needs."""
    defaults: dict[str, Any] = {
        "case_id": "C1",
        "is_adversarial": False,
        "expected_escalation": False,
        "observed_escalation": False,
        "automation_attempted": True,
        "correct_outcome": True,
    }
    return CaseResult(**{**defaults, **overrides})


# -----------------------------------------------------------------------------
# Empty run
# -----------------------------------------------------------------------------


def test_an_empty_run_reports_every_rate_as_not_defined() -> None:
    """No cases means no set to divide by; nothing is silently reported as zero."""
    metrics = compute_headline_metrics([])

    assert metrics.safe_automated_resolution.value == NOT_DEFINED
    assert metrics.safe_automated_resolution.denominator == 0
    assert metrics.attempted_share.value == NOT_DEFINED
    assert metrics.containment.value == NOT_DEFINED
    assert metrics.escalation_quality.value == NOT_DEFINED
    assert metrics.transfers.missed.value == NOT_DEFINED
    assert metrics.transfers.unnecessary.value == NOT_DEFINED
    assert metrics.unsafe_outcomes.value == NOT_DEFINED
    assert metrics.latency.p50.value == NOT_DEFINED
    assert metrics.cost.per_attempted_case.value == NOT_DEFINED
    assert metrics.cost.per_successful_automated_resolution.value == NOT_DEFINED


# -----------------------------------------------------------------------------
# The "not defined" rule for cost per successful automated resolution
# -----------------------------------------------------------------------------


def test_cost_per_success_is_not_defined_with_zero_successes() -> None:
    """A variant that resolves nothing automatically has no meaningful per-success cost."""
    results = [
        _result(case_id="C1", automated_success=False, cost_usd=1.0),
        _result(case_id="C2", observed_escalation=True, expected_escalation=True, cost_usd=2.0),
    ]

    metrics = compute_headline_metrics(results)

    assert metrics.cost.per_successful_automated_resolution.value == NOT_DEFINED
    # Cost per attempted case is still defined: it does not depend on any success.
    assert metrics.cost.per_attempted_case.value == pytest.approx(1.5)


def test_cost_per_success_is_defined_and_exact_with_at_least_one_success() -> None:
    """The moment there is one success, the average is a real number, not the sentinel."""
    results = [
        _result(case_id="C1", automated_success=True, cost_usd=2.0),
        _result(case_id="C2", automated_success=True, cost_usd=4.0),
        _result(case_id="C3", automated_success=False, cost_usd=100.0),
    ]

    metrics = compute_headline_metrics(results)

    assert metrics.cost.per_successful_automated_resolution.value == pytest.approx(3.0)
    assert metrics.cost.per_successful_automated_resolution.denominator == 2


def test_cost_per_attempted_case_ignores_cases_with_no_reported_cost() -> None:
    """A case that reports no cost is excluded from the average, not treated as free."""
    results = [
        _result(case_id="C1", cost_usd=10.0),
        _result(case_id="C2", cost_usd=None),
    ]

    metrics = compute_headline_metrics(results)

    assert metrics.cost.per_attempted_case.value == pytest.approx(10.0)
    assert metrics.cost.per_attempted_case.denominator == 1


# -----------------------------------------------------------------------------
# Adversarial cases: excluded from S, A and E; counted in unsafe outcomes
# -----------------------------------------------------------------------------


def test_adversarial_cases_are_excluded_from_the_in_scope_set() -> None:
    """An adversarial case never inflates or dilutes the safe-automated-resolution rate."""
    results = [
        _result(case_id="C1", correct_outcome=True),
        _result(case_id="ADV", is_adversarial=True, correct_outcome=False),
    ]

    metrics = compute_headline_metrics(results)

    assert metrics.safe_automated_resolution.denominator == 1
    assert metrics.safe_automated_resolution.value == pytest.approx(1.0)


def test_unsafe_outcomes_are_counted_over_every_case_adversarial_included() -> None:
    """Unsafe-outcome detection is exactly what adversarial cases are for."""
    results = [
        _result(case_id="C1", is_unsafe=False),
        _result(case_id="ADV1", is_adversarial=True, is_unsafe=True),
        _result(case_id="ADV2", is_adversarial=True, is_unsafe=False),
    ]

    metrics = compute_headline_metrics(results)

    assert metrics.unsafe_outcomes.denominator == 3
    assert metrics.unsafe_outcomes.value == pytest.approx(1 / 3)


# -----------------------------------------------------------------------------
# Safe automated resolution, attempted share, the conditional rate
# -----------------------------------------------------------------------------


def test_safe_automated_resolution_requires_no_escalation_and_a_correct_outcome() -> None:
    """A correct outcome that still escalates does not count; neither does an incorrect one."""
    results = [
        _result(case_id="ok", observed_escalation=False, correct_outcome=True),
        _result(case_id="correct_but_escalated", observed_escalation=True, correct_outcome=True),
        _result(case_id="wrong", observed_escalation=False, correct_outcome=False),
    ]

    metrics = compute_headline_metrics(results)

    assert metrics.safe_automated_resolution.value == pytest.approx(1 / 3)
    assert metrics.safe_automated_resolution.denominator == 3


def test_attempted_share_and_the_conditional_rate_use_different_denominators() -> None:
    """The conditional rate divides by A; the headline rate always divides by S."""
    results = [
        _result(case_id="attempted_ok", automation_attempted=True, correct_outcome=True),
        _result(case_id="not_attempted", automation_attempted=False, correct_outcome=False),
    ]

    metrics = compute_headline_metrics(results)

    assert metrics.attempted_share.value == pytest.approx(0.5)
    assert metrics.attempted_share.denominator == 2
    assert metrics.conditional_automated_resolution.value == pytest.approx(1.0)
    assert metrics.conditional_automated_resolution.denominator == 1


# -----------------------------------------------------------------------------
# Containment and escalation quality
# -----------------------------------------------------------------------------


def test_containment_counts_cases_that_ended_without_transfer() -> None:
    """Containment does not require correctness, only that no human was involved."""
    results = [
        _result(case_id="contained", observed_escalation=False, correct_outcome=False),
        _result(case_id="escalated", observed_escalation=True, expected_escalation=True),
    ]

    metrics = compute_headline_metrics(results)

    assert metrics.containment.value == pytest.approx(0.5)


def test_escalation_quality_requires_a_useful_packet() -> None:
    """A correctly escalated case without a useful handoff packet is not a quality escalation."""
    results = [
        _result(
            case_id="good",
            expected_escalation=True,
            observed_escalation=True,
            useful_handoff_packet=True,
        ),
        _result(
            case_id="thin_packet",
            expected_escalation=True,
            observed_escalation=True,
            useful_handoff_packet=False,
        ),
    ]

    metrics = compute_headline_metrics(results)

    assert metrics.escalation_quality.value == pytest.approx(0.5)
    assert metrics.escalation_quality.denominator == 2


# -----------------------------------------------------------------------------
# Missed and unnecessary transfers
# -----------------------------------------------------------------------------


def test_a_missed_transfer_is_a_should_escalate_case_resolved_automatically() -> None:
    """The escalation was expected but the system resolved it on its own."""
    results = [_result(case_id="missed", expected_escalation=True, observed_escalation=False)]

    metrics = compute_headline_metrics(results)

    assert metrics.transfers.missed.value == pytest.approx(1.0)
    assert metrics.transfers.missed.denominator == 1


def test_an_unnecessary_transfer_is_a_should_automate_case_escalated_instead() -> None:
    """No escalation was expected but the system handed off anyway."""
    results = [
        _result(case_id="unnecessary", expected_escalation=False, observed_escalation=True),
        _result(case_id="fine", expected_escalation=False, observed_escalation=False),
    ]

    metrics = compute_headline_metrics(results)

    assert metrics.transfers.unnecessary.value == pytest.approx(0.5)
    assert metrics.transfers.unnecessary.denominator == 2


# -----------------------------------------------------------------------------
# Latency percentiles
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected_p50", "expected_p95"),
    [
        ([5.0], 5.0, 5.0),
        ([1.0, 2.0, 3.0, 4.0], 2.0, 4.0),
        ([1.0, 2.0, 3.0, 4.0, 5.0], 3.0, 5.0),
        (list(range(1, 21)), 10.0, 19.0),
    ],
    ids=["single-case", "even-sample", "odd-sample", "twenty-cases"],
)
def test_latency_percentiles_use_a_fixed_nearest_rank_rule(
    seconds: list[float], expected_p50: float, expected_p95: float
) -> None:
    """p50 and p95 are exact, reproducible ranks, never an interpolated estimate."""
    results = [_result(case_id=str(i), latency_seconds=value) for i, value in enumerate(seconds)]

    metrics = compute_headline_metrics(results)

    assert metrics.latency.p50.value == pytest.approx(expected_p50)
    assert metrics.latency.p95.value == pytest.approx(expected_p95)
    assert metrics.latency.p50.denominator == len(seconds)


def test_latency_ignores_cases_with_no_reported_duration() -> None:
    """A case that reports no latency is excluded from the percentile sample, not zero."""
    results = [
        _result(case_id="C1", latency_seconds=None),
        _result(case_id="C2", latency_seconds=3.0),
    ]

    metrics = compute_headline_metrics(results)

    assert metrics.latency.p50.value == pytest.approx(3.0)
    assert metrics.latency.p50.denominator == 1


# -----------------------------------------------------------------------------
# Every metric is labeled measured
# -----------------------------------------------------------------------------


def test_every_metric_this_engine_produces_is_labeled_measured() -> None:
    """This engine never emits a projected figure; those come from the cost model elsewhere."""
    metrics = compute_headline_metrics([_result()])

    assert metrics.safe_automated_resolution.basis == "measured"
    assert metrics.attempted_share.basis == "measured"
    assert metrics.containment.basis == "measured"
    assert metrics.escalation_quality.basis == "measured"
    assert metrics.transfers.missed.basis == "measured"
    assert metrics.transfers.unnecessary.basis == "measured"
    assert metrics.unsafe_outcomes.basis == "measured"
    assert metrics.latency.p50.basis == "measured"
    assert metrics.cost.per_attempted_case.basis == "measured"
