"""
Judge Validation Tests
=======================

Component: ``evals.judge_validation``. Hermetic and pure: every case is built in memory.
"""

from __future__ import annotations

from evals.judge import JudgeVerdict
from evals.judge_validation import DEMOTION_THRESHOLD, RaterScore, compute_agreement

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
