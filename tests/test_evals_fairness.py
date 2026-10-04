"""
Fairness Slicing Tests
=======================

Component: ``evals.fairness``. Pure and hermetic: slice sizes, the small-sample marker, the
disparity flag (non-overlapping Wilson intervals) and the accent-flavored comparison.
"""

from __future__ import annotations

# Standard libraries
from typing import Any

# Local modules
from app.domain.policy.models import DisputeCategory
from contracts.service_v1.envelope import Intent
from evals.fairness import (
    ACCENT,
    ACCENT_FLAVORED,
    COUNTRY,
    LANGUAGE,
    OTHER_SPANISH,
    SEGMENT,
    SMALL_SAMPLE_THRESHOLD,
    UNKNOWN,
    CaseProfile,
    SliceRow,
    slice_results,
)
from evals.golden.multilingual import ACCENT_FLAVORED_CASE_IDS
from evals.metrics import NOT_DEFINED, CaseResult
from evals.models import Case, CaseCategory, SafeBehavior

_ACCENT_ID = sorted(ACCENT_FLAVORED_CASE_IDS)[0]


def _case(case_id: str, lang: str = "es", category: CaseCategory = CaseCategory.NORMAL) -> Case:
    values: dict[str, Any] = {
        "case_id": case_id,
        "category": category,
        "lang": lang,
        "provenance": "observed",
        "seed_ref": "ops_seed:TRX-1",
        "user_turns": ("hola",),
        "expected_intent": Intent.CONFIRM_FILING,
        "expected_category": DisputeCategory.UNRECOGNIZED_CHARGE,
    }
    if category is CaseCategory.ADVERSARIAL:
        values["expected_safe_behavior"] = SafeBehavior.REFUSE
        values["expected_intent"] = None
        values["expected_category"] = None
    return Case(**values)


def _result(case_id: str, *, correct: bool = True, **overrides: Any) -> CaseResult:
    values: dict[str, Any] = {
        "case_id": case_id,
        "is_adversarial": False,
        "expected_escalation": False,
        "observed_escalation": False,
        "automation_attempted": True,
        "correct_outcome": correct,
    }
    return CaseResult(**{**values, **overrides})


def _row(analysis_rows: tuple[SliceRow, ...], dimension: str, label: str) -> SliceRow:
    return next(r for r in analysis_rows if r.dimension == dimension and r.label == label)


def _population(
    lang: str, size: int, failing: int, prefix: str
) -> tuple[list[Case], list[CaseResult]]:
    ids = [f"{prefix}-{i}" for i in range(size)]
    cases = [_case(case_id, lang) for case_id in ids]
    results = [_result(case_id, correct=i >= failing) for i, case_id in enumerate(ids)]
    return cases, results


def test_a_slice_states_how_many_cases_stand_behind_it() -> None:
    cases = [_case("a", "es"), _case("b", "es"), _case("c", "pt")]
    results = [_result("a"), _result("b", correct=False), _result("c")]

    analysis = slice_results(results, cases, {})

    spanish = _row(analysis.rows, LANGUAGE, "es")
    assert (spanish.cases, spanish.in_scope) == (2, 2)
    assert spanish.correct_outcome.value == 0.5
    assert spanish.correct_outcome.denominator == 2
    assert _row(analysis.rows, LANGUAGE, "pt").cases == 1


def test_adversarial_cases_count_as_cases_but_not_in_scope() -> None:
    cases = [_case("a"), _case("x", category=CaseCategory.ADVERSARIAL)]
    results = [_result("a"), _result("x", is_adversarial=True, is_unsafe=True)]

    row = _row(slice_results(results, cases, {}).rows, LANGUAGE, "es")

    assert (row.cases, row.in_scope, row.unsafe) == (2, 1, 1)


def test_a_slice_below_the_threshold_is_marked_a_small_sample() -> None:
    cases, results = _population("es", SMALL_SAMPLE_THRESHOLD, 0, "e")
    small_cases, small_results = _population("pt", SMALL_SAMPLE_THRESHOLD - 1, 0, "p")

    rows = slice_results(results + small_results, cases + small_cases, {}).rows

    assert not _row(rows, LANGUAGE, "es").small_sample
    assert _row(rows, LANGUAGE, "pt").small_sample


def test_a_slice_with_no_in_scope_case_has_an_undefined_rate() -> None:
    cases = [_case("a"), _case("x", "pt", CaseCategory.ADVERSARIAL)]
    results = [_result("a"), _result("x", is_adversarial=True)]

    row = _row(slice_results(results, cases, {}).rows, LANGUAGE, "pt")

    assert row.correct_outcome.value == NOT_DEFINED


def test_country_and_segment_slices_use_the_profiles_and_keep_unknown_last() -> None:
    cases = [_case("a"), _case("b"), _case("c")]
    results = [_result("a"), _result("b"), _result("c")]
    profiles = {"a": CaseProfile("MX", "Plus"), "b": CaseProfile("CO", "Basic")}

    rows = slice_results(results, cases, profiles).rows

    countries = [r.label for r in rows if r.dimension == COUNTRY]
    assert countries == ["CO", "MX", UNKNOWN]
    assert _row(rows, SEGMENT, UNKNOWN).cases == 1
    assert [r.label for r in rows if r.dimension == SEGMENT][-1] == UNKNOWN


def test_a_case_with_no_golden_entry_is_counted_in_an_unknown_language() -> None:
    row = _row(slice_results([_result("orphan")], [], {}).rows, LANGUAGE, UNKNOWN)

    assert row.cases == 1


def test_a_large_gap_is_flagged_with_its_failing_cases_and_their_categories() -> None:
    es_cases, es_results = _population("es", 60, 0, "e")
    pt_cases, pt_results = _population("pt", 60, 30, "p")

    analysis = slice_results(es_results + pt_results, es_cases + pt_cases, {})

    flagged = {(d.dimension, d.label): d for d in analysis.disparities}
    portuguese = flagged[(LANGUAGE, "pt")]
    assert portuguese.rate == 0.5
    assert portuguese.comparison_rate == 1.0
    assert (portuguese.in_scope, portuguese.comparison_in_scope) == (60, 60)
    assert len(portuguese.failing_case_ids) == 30
    assert portuguese.failing_categories == (("normal", 30),)
    assert (LANGUAGE, "es") in flagged


def test_the_same_gap_in_a_small_sample_is_not_flagged() -> None:
    es_cases, es_results = _population("es", 4, 0, "e")
    pt_cases, pt_results = _population("pt", 4, 2, "p")

    analysis = slice_results(es_results + pt_results, es_cases + pt_cases, {})

    assert analysis.disparities == ()


def test_a_small_gap_between_large_slices_is_not_flagged() -> None:
    es_cases, es_results = _population("es", 200, 10, "e")
    pt_cases, pt_results = _population("pt", 200, 12, "p")

    assert slice_results(es_results + pt_results, es_cases + pt_cases, {}).disparities == ()


def test_the_flag_follows_correct_outcome_not_safe_automated_resolution() -> None:
    """A correct hand-off is a miss for safe automated resolution; it must not trigger a flag."""
    es_cases, es_results = _population("es", 60, 0, "e")
    pt_cases = [_case(f"p-{i}", "pt") for i in range(60)]
    pt_results = [
        _result(f"p-{i}", observed_escalation=True, expected_escalation=True, correct=True)
        for i in range(60)
    ]

    analysis = slice_results(es_results + pt_results, es_cases + pt_cases, {})

    portuguese = _row(analysis.rows, LANGUAGE, "pt")
    assert portuguese.safe_automated_resolution.value == 0.0
    assert portuguese.correct_outcome.value == 1.0
    assert analysis.disparities == ()


def test_the_accent_flavored_subset_is_compared_with_the_other_spanish_cases() -> None:
    other_cases, other_results = _population("es", 5, 0, "o")
    portuguese = _case("p-1", "pt")
    accent = _case(_ACCENT_ID, "es", CaseCategory.MULTILINGUAL)
    cases = [*other_cases, portuguese, accent]
    results = [*other_results, _result("p-1", correct=False), _result(_ACCENT_ID, correct=False)]

    rows = slice_results(results, cases, {}).rows

    flavored = _row(rows, ACCENT, ACCENT_FLAVORED)
    other = _row(rows, ACCENT, OTHER_SPANISH)
    assert (flavored.cases, other.cases) == (1, 5)
    assert flavored.correct_outcome.value == 0.0
    assert other.correct_outcome.value == 1.0


def test_an_accent_gap_beyond_noise_is_flagged() -> None:
    ids = sorted(ACCENT_FLAVORED_CASE_IDS)
    other_cases, other_results = _population("es", 400, 0, "o")
    accent_cases = [_case(i, "es", CaseCategory.MULTILINGUAL) for i in ids]
    accent_results = [_result(i, correct=False) for i in ids]

    analysis = slice_results(other_results + accent_results, other_cases + accent_cases, {})

    flagged = [d for d in analysis.disparities if d.dimension == ACCENT]
    assert len(flagged) == 1
    assert flagged[0].failing_categories == (("multilingual", len(ids)),)
