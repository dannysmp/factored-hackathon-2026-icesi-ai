"""
Judge Validation Sample Tests
===============================

Component: ``evals.golden.judge_validation_sample``. Hermetic and pure: proves the synthetic H4
placeholder actually exercises all three of ``evals.judge_validation.compute_agreement``'s decision
branches (full agreement, a genuine demotion, and the NA-exclusion path) — not just that
``compute_agreement`` runs against it without crashing.
"""

from __future__ import annotations

from dataclasses import replace

from evals.golden.judge_validation_sample import (
    JUDGE_VERDICTS,
    PROVENANCE,
    RATER_1_SCORES,
    RATER_2_SCORES,
)
from evals.judge_validation import compute_agreement


def test_the_sample_is_labeled_synthetic() -> None:
    assert PROVENANCE == "team_generated_synthetic"


def test_the_sample_holds_fifty_cases_per_source() -> None:
    assert len(RATER_1_SCORES) == 50
    assert len(RATER_2_SCORES) == 50
    assert len(JUDGE_VERDICTS) == 50


def test_case_ids_are_unique_and_shared_across_all_three_sources() -> None:
    ids_1 = {score.case_id for score in RATER_1_SCORES}
    ids_2 = {score.case_id for score in RATER_2_SCORES}
    ids_judge = {verdict.case_id for verdict in JUDGE_VERDICTS}
    assert len(ids_1) == 50
    assert ids_1 == ids_2 == ids_judge


def test_grounding_is_full_agreement_and_not_demoted() -> None:
    results = compute_agreement(RATER_1_SCORES, RATER_2_SCORES, JUDGE_VERDICTS)
    grounding = next(r for r in results if r.dimension == "grounding")

    assert grounding.rater_to_rater == 1.0
    assert grounding.rater1_to_judge == 1.0
    assert grounding.demoted is False


def test_language_quality_is_demoted_by_a_genuine_judge_disagreement() -> None:
    results = compute_agreement(RATER_1_SCORES, RATER_2_SCORES, JUDGE_VERDICTS)
    language_quality = next(r for r in results if r.dimension == "language_quality")

    assert language_quality.rater_to_rater == 1.0  # the humans still agree with each other
    assert isinstance(language_quality.rater1_to_judge, float)
    assert language_quality.rater1_to_judge < 0.8
    assert language_quality.demoted is True


def test_reverting_the_judges_disagreement_undoes_the_demotion() -> None:
    """Revert-check: the demotion above is caused by the injected disagreement, not a bug that
    demotes every dimension regardless of the data."""
    agreeing_judge = tuple(replace(verdict, language_quality=2) for verdict in JUDGE_VERDICTS)

    results = compute_agreement(RATER_1_SCORES, RATER_2_SCORES, agreeing_judge)
    language_quality = next(r for r in results if r.dimension == "language_quality")
    grounding = next(r for r in results if r.dimension == "grounding")

    assert language_quality.rater1_to_judge == 1.0
    assert language_quality.demoted is False
    assert grounding.demoted is False  # unaffected by the revert, as it should be


def test_clarification_is_scored_only_for_the_ambiguous_subset_and_fully_agrees() -> None:
    results = compute_agreement(RATER_1_SCORES, RATER_2_SCORES, JUDGE_VERDICTS)
    clarification = next(r for r in results if r.dimension == "clarification")

    scored = [score for score in RATER_1_SCORES if score.clarification is not None]
    assert len(scored) == 15
    assert clarification.rater_to_rater == 1.0
    assert clarification.demoted is False
