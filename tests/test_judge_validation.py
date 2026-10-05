"""
Judge Validation Tests
=======================

Component: ``evals.judge_validation``. Hermetic and pure: every case is built in memory.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from evals.judge import JudgeVerdict
from evals.judge_validation import (
    DEMOTION_THRESHOLD,
    RaterScore,
    compute_agreement,
    compute_detail,
    compute_human_means,
)

_MODEL = "claude-sonnet-5"


def _rater(
    case_id: str,
    role: str,
    *,
    grounding: int,
    language_quality: int,
    clarification: int | None = None,
) -> RaterScore:
    return RaterScore(
        case_id=case_id,
        role=role,  # type: ignore[arg-type]
        grounding=grounding,
        language_quality=language_quality,
        clarification=clarification,
    )


def _judge(
    case_id: str, *, grounding: int, language_quality: int, clarification: int | None = None
) -> JudgeVerdict:
    return JudgeVerdict(
        case_id=case_id,
        grounding=grounding,
        language_quality=language_quality,
        clarification=clarification,
        rationale="x",
        judge_model=_MODEL,
        prompt_version="1",
    )


def test_full_agreement_on_every_dimension_is_not_demoted() -> None:
    rater1 = [_rater("C1", "Rater 1", grounding=2, language_quality=2, clarification=1)]
    rater2 = [_rater("C1", "Rater 2", grounding=2, language_quality=2, clarification=1)]
    judge = [_judge("C1", grounding=2, language_quality=2, clarification=1)]

    results = compute_agreement(rater1, rater2, judge)

    assert all(r.rater_to_rater == 1.0 for r in results)
    assert all(r.rater1_to_judge == 1.0 for r in results)
    assert all(r.rater2_to_judge == 1.0 for r in results)
    assert all(not r.demoted for r in results)


def test_a_dimension_below_the_threshold_is_demoted() -> None:
    """Revert-check: this must fail once any score below the threshold is changed to agree."""
    rater1 = [
        _rater(f"C{i}", "Rater 1", grounding=2, language_quality=2, clarification=None)
        for i in range(10)
    ]
    rater2 = [
        _rater(f"C{i}", "Rater 2", grounding=2, language_quality=2, clarification=None)
        for i in range(10)
    ]
    # The judge disagrees with both raters on grounding for most cases: below DEMOTION_THRESHOLD.
    judge = [
        _judge(f"C{i}", grounding=0 if i < 8 else 2, language_quality=2, clarification=None)
        for i in range(10)
    ]

    results = compute_agreement(rater1, rater2, judge)
    grounding = next(r for r in results if r.dimension == "grounding")
    language_quality = next(r for r in results if r.dimension == "language_quality")

    assert grounding.rater1_to_judge == 0.2
    assert grounding.rater2_to_judge == 0.2
    assert grounding.demoted is True
    # The un-manipulated dimension is the revert check: it must NOT also be demoted, proving the
    # demotion above is caused by the grounding mismatch, not a bug that demotes everything.
    assert language_quality.demoted is False


def test_the_threshold_is_exactly_eighty_percent_inclusive() -> None:
    """Agreement exactly at the threshold is not demoted; one point below is."""
    at_threshold = int(DEMOTION_THRESHOLD * 10)  # 8 of 10 agree
    rater1 = [_rater(f"C{i}", "Rater 1", grounding=2, language_quality=2) for i in range(10)]
    rater2 = [_rater(f"C{i}", "Rater 2", grounding=2, language_quality=2) for i in range(10)]
    judge = [
        _judge(f"C{i}", grounding=2 if i < at_threshold else 0, language_quality=2)
        for i in range(10)
    ]

    results = compute_agreement(rater1, rater2, judge)
    grounding = next(r for r in results if r.dimension == "grounding")

    assert grounding.rater1_to_judge == DEMOTION_THRESHOLD
    assert grounding.demoted is False


def test_clarification_is_compared_only_where_both_sides_scored_it() -> None:
    rater1 = [
        _rater("C1", "Rater 1", grounding=2, language_quality=2, clarification=None),
        _rater("C2", "Rater 1", grounding=2, language_quality=2, clarification=1),
    ]
    rater2 = [
        _rater("C1", "Rater 2", grounding=2, language_quality=2, clarification=None),
        _rater("C2", "Rater 2", grounding=2, language_quality=2, clarification=0),
    ]
    judge = [
        _judge("C1", grounding=2, language_quality=2, clarification=None),
        _judge("C2", grounding=2, language_quality=2, clarification=1),
    ]

    results = compute_agreement(rater1, rater2, judge)
    clarification = next(r for r in results if r.dimension == "clarification")

    # Only C2 has a clarification score on both sides; C1's NA is excluded from the denominator.
    assert clarification.rater_to_rater == 0.0
    assert clarification.rater1_to_judge == 1.0


def test_a_dimension_with_zero_comparable_cases_is_not_defined_and_is_demoted() -> None:
    rater1 = [_rater("C1", "Rater 1", grounding=2, language_quality=2, clarification=None)]
    rater2 = [_rater("C1", "Rater 2", grounding=2, language_quality=2, clarification=None)]
    judge = [_judge("C1", grounding=2, language_quality=2, clarification=None)]

    results = compute_agreement(rater1, rater2, judge)
    clarification = next(r for r in results if r.dimension == "clarification")

    assert clarification.rater_to_rater == "not defined"
    assert clarification.demoted is True


def test_a_case_id_missing_from_one_side_is_excluded_from_that_comparison() -> None:
    rater1 = [
        _rater("C1", "Rater 1", grounding=2, language_quality=2),
        _rater("C2", "Rater 1", grounding=0, language_quality=2),
    ]
    rater2 = [_rater("C1", "Rater 2", grounding=2, language_quality=2)]  # C2 never returned
    judge = [
        _judge("C1", grounding=2, language_quality=2),
        _judge("C2", grounding=0, language_quality=2),
    ]

    results = compute_agreement(rater1, rater2, judge)
    grounding = next(r for r in results if r.dimension == "grounding")

    # rater_to_rater only has C1 to compare (full agreement); rater1_to_judge has both (also full).
    assert grounding.rater_to_rater == 1.0
    assert grounding.rater1_to_judge == 1.0


def _grounding_only(
    first: Sequence[int], second: Sequence[int]
) -> tuple[list[RaterScore], list[JudgeVerdict]]:
    rater = [
        _rater(f"C{n}", "Rater 1", grounding=score, language_quality=0)
        for n, score in enumerate(first)
    ]
    judge = [_judge(f"C{n}", grounding=score, language_quality=0) for n, score in enumerate(second)]
    return rater, judge


def _grounding_kappa(first: Sequence[int], second: Sequence[int]) -> float | str:
    rater, judge = _grounding_only(first, second)
    detail = compute_detail(rater, rater, judge)[0]
    assert detail.dimension == "grounding"
    return detail.rater1_to_judge.weighted_kappa


def test_weighted_kappa_is_one_for_identical_scores_with_spread() -> None:
    assert _grounding_kappa([0, 1, 2, 2], [0, 1, 2, 2]) == pytest.approx(1.0)


def test_weighted_kappa_matches_a_hand_computed_value() -> None:
    # Observed weighted disagreement 0.25 (one 2-vs-1 pair, weight 1/4); expected 5.0 / 4 = 1.25.
    assert _grounding_kappa([0, 1, 2, 2], [0, 1, 1, 2]) == pytest.approx(0.8)


def test_weighted_kappa_is_minus_one_for_complete_opposition() -> None:
    assert _grounding_kappa([0, 2], [2, 0]) == pytest.approx(-1.0)


def test_weighted_kappa_is_zero_when_one_side_never_varies() -> None:
    assert _grounding_kappa([0, 1, 2], [1, 1, 1]) == pytest.approx(0.0)


def test_weighted_kappa_is_zero_when_both_sides_are_constant_at_different_scores() -> None:
    assert _grounding_kappa([0, 0, 0, 0], [2, 2, 2, 2]) == pytest.approx(0.0)


def test_weighted_kappa_is_not_defined_when_both_sides_give_one_score() -> None:
    assert _grounding_kappa([2, 2, 2], [2, 2, 2]) == "not defined"


def test_weighted_kappa_penalizes_a_two_point_gap_more_than_a_one_point_gap() -> None:
    near = _grounding_kappa([0, 1, 2, 1], [1, 1, 2, 1])
    far = _grounding_kappa([0, 1, 2, 1], [2, 1, 2, 1])
    assert isinstance(near, float) and isinstance(far, float)
    assert near > far


def test_detail_counts_which_side_scored_higher_where_they_differ() -> None:
    rater, judge = _grounding_only([2, 2, 1, 0, 1], [1, 0, 2, 0, 1])
    pair = compute_detail(rater, rater, judge)[0].rater1_to_judge

    assert pair.compared == 5
    assert pair.first_higher == 2  # the rater scored above the judge twice
    assert pair.second_higher == 1


def test_detail_compares_clarification_only_where_both_sides_scored_it() -> None:
    rater1 = [
        _rater("C1", "Rater 1", grounding=2, language_quality=2, clarification=2),
        _rater("C2", "Rater 1", grounding=2, language_quality=2, clarification=None),
        _rater("C3", "Rater 1", grounding=2, language_quality=2, clarification=0),
    ]
    rater2 = [
        _rater("C1", "Rater 2", grounding=2, language_quality=2, clarification=2),
        _rater("C2", "Rater 2", grounding=2, language_quality=2, clarification=None),
        _rater("C3", "Rater 2", grounding=2, language_quality=2, clarification=1),
    ]
    judge = [
        _judge("C1", grounding=2, language_quality=2, clarification=2),
        _judge("C2", grounding=2, language_quality=2, clarification=1),
        _judge("C3", grounding=2, language_quality=2, clarification=None),
    ]
    clarification = {d.dimension: d for d in compute_detail(rater1, rater2, judge)}["clarification"]

    assert clarification.rater_to_rater.compared == 2
    assert clarification.rater1_to_judge.compared == 1  # C2 is NA for the rater, C3 for the judge
    assert clarification.rater_to_rater.second_higher == 1


def test_detail_pair_counts_are_the_denominators_of_the_agreement_rates() -> None:
    rater1 = [_rater("C1", "Rater 1", grounding=2, language_quality=1)]
    rater2 = [_rater("C1", "Rater 2", grounding=2, language_quality=1)]
    judge = [_judge("C1", grounding=2, language_quality=2)]

    detail = compute_detail(rater1, rater2, judge)
    agreement = compute_agreement(rater1, rater2, judge)

    assert [d.dimension for d in detail] == [a.dimension for a in agreement]
    clarification = detail[2].rater1_to_judge
    assert clarification.compared == 0 and clarification.weighted_kappa == "not defined"
    assert agreement[2].rater1_to_judge == "not defined"


def test_human_means_are_each_raters_own_mean_over_the_cases_they_scored() -> None:
    rater1 = [
        _rater("J-1", "Rater 1", grounding=2, language_quality=1, clarification=2),
        _rater("J-2", "Rater 1", grounding=1, language_quality=1),
    ]
    rater2 = [
        _rater("J-1", "Rater 2", grounding=0, language_quality=2, clarification=1),
        _rater("J-2", "Rater 2", grounding=1, language_quality=2),
    ]

    means = {mean.dimension: mean for mean in compute_human_means(rater1, rater2)}

    assert means["grounding"].rater1_mean == 1.5
    assert means["grounding"].rater2_mean == 0.5
    assert means["language_quality"].rater2_mean == 2.0
    assert (means["clarification"].rater1_mean, means["clarification"].rater1_scored) == (2.0, 1)
    assert (means["clarification"].rater2_mean, means["clarification"].rater2_scored) == (1.0, 1)


def test_a_dimension_neither_rater_scored_has_no_human_mean() -> None:
    rater1 = [_rater("J-1", "Rater 1", grounding=2, language_quality=1)]
    rater2 = [_rater("J-1", "Rater 2", grounding=2, language_quality=1)]

    means = {mean.dimension: mean for mean in compute_human_means(rater1, rater2)}

    assert means["clarification"].rater1_mean is None
    assert means["clarification"].rater2_scored == 0
