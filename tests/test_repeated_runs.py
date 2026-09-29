"""
Repeated-Run Variability Tests
=================================

Component: ``evals.repeated_runs``. Hermetic: every run is built in memory.
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
    CostMetrics,
    HeadlineMetrics,
    LatencyMetrics,
    Metric,
    TransferCounts,
)
from evals.repeated_runs import (
    UnsafeOccurrence,
    compute_variability,
    flipped_cases,
    unsafe_occurrences,
)


def _result(**overrides: Any) -> CaseResult:
    defaults: dict[str, Any] = {
        "case_id": "C1",
        "is_adversarial": False,
        "expected_escalation": False,
        "observed_escalation": False,
        "automation_attempted": True,
        "correct_outcome": True,
    }
    return CaseResult(**{**defaults, **overrides})


def _metric(value: float | str, denominator: int = 10) -> Metric:
    return Metric(value, basis="measured", denominator=denominator)  # type: ignore[arg-type]


def _headline(*, safe: float | str = 0.5) -> HeadlineMetrics:
    return HeadlineMetrics(
        safe_automated_resolution=_metric(safe),
        attempted_share=_metric(0.9),
        conditional_automated_resolution=_metric(safe),
        containment=_metric(0.8),
        escalation_quality=_metric(0.7),
        transfers=TransferCounts(missed=_metric(0.1), unnecessary=_metric(0.05)),
        unsafe_outcomes=_metric(0.0),
        latency=LatencyMetrics(p50=_metric(1.0), p95=_metric(2.0)),
        cost=CostMetrics(
            per_attempted_case=_metric(0.02), per_successful_automated_resolution=_metric(0.05)
        ),
    )


# -----------------------------------------------------------------------------
# compute_variability
# -----------------------------------------------------------------------------


def test_variability_needs_at_least_one_run() -> None:
    with pytest.raises(ValueError, match="at least one run"):
        compute_variability([])


def test_mean_low_high_across_three_runs() -> None:
    runs = [_headline(safe=0.4), _headline(safe=0.6), _headline(safe=0.5)]

    result = compute_variability(runs)

    assert result.safe_automated_resolution.mean == pytest.approx(0.5)
    assert result.safe_automated_resolution.low == pytest.approx(0.4)
    assert result.safe_automated_resolution.high == pytest.approx(0.6)


def test_a_single_run_has_a_zero_width_range() -> None:
    result = compute_variability([_headline(safe=0.5)])

    assert result.safe_automated_resolution.mean == pytest.approx(0.5)
    assert result.safe_automated_resolution.low == result.safe_automated_resolution.high


def test_not_defined_in_any_run_makes_the_variability_not_defined() -> None:
    """Averaging over only the defined runs would understate real instability; it does not."""
    runs = [_headline(safe=0.4), _headline(safe=NOT_DEFINED), _headline(safe=0.6)]

    result = compute_variability(runs)

    assert result.safe_automated_resolution.mean == NOT_DEFINED
    assert result.safe_automated_resolution.low == NOT_DEFINED
    assert result.safe_automated_resolution.high == NOT_DEFINED


def test_every_headline_metric_field_is_covered() -> None:
    """Every field HeadlineMetrics reports has a matching variability field, not just the one
    this test file happens to exercise elsewhere."""
    runs = [_headline(), _headline()]

    result = compute_variability(runs)

    for field in (
        "safe_automated_resolution",
        "attempted_share",
        "conditional_automated_resolution",
        "containment",
        "escalation_quality",
        "missed_transfers",
        "unnecessary_transfers",
        "unsafe_outcomes",
        "latency_p50",
        "latency_p95",
        "cost_per_attempted_case",
        "cost_per_successful_automated_resolution",
    ):
        assert isinstance(getattr(result, field).mean, float)


# -----------------------------------------------------------------------------
# flipped_cases
# -----------------------------------------------------------------------------


def test_a_case_correct_in_every_run_is_not_a_flip() -> None:
    runs = [[_result(correct_outcome=True)], [_result(correct_outcome=True)]]

    assert flipped_cases(runs) == ()


def test_a_case_that_flips_correctness_across_runs_is_reported() -> None:
    runs = [
        [_result(case_id="C1", correct_outcome=True)],
        [_result(case_id="C1", correct_outcome=False)],
    ]

    flips = flipped_cases(runs)

    assert len(flips) == 1
    assert flips[0].case_id == "C1"
    assert flips[0].correct_outcome_by_run == (True, False)


def test_a_case_that_flips_safety_across_runs_is_reported() -> None:
    runs = [
        [_result(case_id="C1", is_unsafe=False)],
        [_result(case_id="C1", is_unsafe=True)],
    ]

    flips = flipped_cases(runs)

    assert len(flips) == 1
    assert flips[0].is_unsafe_by_run == (False, True)


def test_a_case_missing_from_a_run_is_reported_as_a_flip_with_none() -> None:
    runs = [[_result(case_id="C1", correct_outcome=True)], []]

    flips = flipped_cases(runs)

    assert len(flips) == 1
    assert flips[0].correct_outcome_by_run == (True, None)


def test_flipped_cases_on_a_single_run_is_always_empty() -> None:
    runs = [[_result(case_id="C1", correct_outcome=True), _result(case_id="C2")]]

    assert flipped_cases(runs) == ()


# -----------------------------------------------------------------------------
# unsafe_occurrences
# -----------------------------------------------------------------------------


def test_no_unsafe_results_gives_no_occurrences() -> None:
    runs = [[_result(is_unsafe=False)], [_result(is_unsafe=False)]]

    assert unsafe_occurrences(runs) == ()


def test_an_unsafe_result_in_one_run_is_reported_with_its_run_index() -> None:
    unsafe = _result(case_id="C1", is_unsafe=True, unsafe_reasons=("pii_leaked",))
    runs = [[_result(case_id="C1", is_unsafe=False)], [unsafe], [_result(case_id="C1")]]

    occurrences = unsafe_occurrences(runs)

    assert occurrences == (UnsafeOccurrence(run_index=1, result=unsafe),)


def test_an_unsafe_result_repeated_across_runs_is_one_entry_per_run() -> None:
    """Never deduplicated by case id: two unsafe runs of the same case are two entries."""
    run0_unsafe = _result(case_id="C1", is_unsafe=True, unsafe_reasons=("unbacked_handoff",))
    run2_unsafe = _result(case_id="C1", is_unsafe=True, unsafe_reasons=("pii_leaked",))
    runs = [[run0_unsafe], [_result(case_id="C1", is_unsafe=False)], [run2_unsafe]]

    occurrences = unsafe_occurrences(runs)

    assert occurrences == (
        UnsafeOccurrence(run_index=0, result=run0_unsafe),
        UnsafeOccurrence(run_index=2, result=run2_unsafe),
    )
