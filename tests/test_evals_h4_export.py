"""
Human Judge-Validation Sample Export Tests
==========================================

Component: ``evals.golden.h4_export``. Hermetic and pure throughout: no store, no clock, no
network call, no dependency on the real golden set beyond one test that locks in its real
135-case mix's own allocation as a regression check.
"""

from __future__ import annotations

# Standard libraries
import csv
from pathlib import Path
from typing import Any

# Local modules
from app.domain.policy.models import DisputeCategory
from contracts.service_v1.envelope import Intent
from evals.golden.case_sheet import ALL_CASES
from evals.golden.h4_export import build_h4_rows, select_stratified_sample, write_h4_case_sheet
from evals.metrics import CaseResult
from evals.models import Case, CaseCategory, SafeBehavior


def _case(**overrides: Any) -> Case:
    values: dict[str, Any] = {
        "case_id": "c1",
        "category": CaseCategory.NORMAL,
        "lang": "es",
        "provenance": "observed",
        "seed_ref": "ops_seed:TRX-1",
        "user_turns": ("No reconozco un cargo.",),
        "expected_intent": Intent.CONFIRM_FILING,
        "expected_category": DisputeCategory.UNRECOGNIZED_CHARGE,
    }
    return Case(**{**values, **overrides})


def _cases(category: CaseCategory, count: int, *, prefix: str) -> tuple[Case, ...]:
    overrides: dict[str, Any] = (
        {
            "expected_intent": Intent.REFUSE,
            "expected_category": None,
            "expected_safe_behavior": SafeBehavior.REFUSE,
        }
        if category is CaseCategory.ADVERSARIAL
        else {}
    )
    return tuple(
        _case(
            case_id=f"{prefix}-{n:02d}",
            category=category,
            seed_ref=f"ops_seed:TRX-{n}",
            **overrides,
        )
        for n in range(count)
    )


# -----------------------------------------------------------------------------
# select_stratified_sample — proportional, deterministic
# -----------------------------------------------------------------------------


def test_the_sample_size_is_exact_even_with_fractional_shares() -> None:
    # 10 normal, 10 adversarial: an even split, no remainder to resolve.
    cases = _cases(CaseCategory.NORMAL, 10, prefix="n") + _cases(
        CaseCategory.ADVERSARIAL, 10, prefix="a"
    )

    sample = select_stratified_sample(cases, sample_size=10)

    assert len(sample) == 10
    assert sum(1 for c in sample if c.category is CaseCategory.NORMAL) == 5
    assert sum(1 for c in sample if c.category is CaseCategory.ADVERSARIAL) == 5


def test_largest_remainder_breaks_ties_by_category_declaration_order() -> None:
    # 3 categories, 10 cases each (30 total), sample_size=10: exact share is 10/3 = 3.333 each,
    # floor 3 each = 9, one seat left over. Every category ties on remainder (.333...), so the
    # tie-break is CaseCategory's own declaration order: normal comes before ambiguous and
    # unsupported, so normal gets the extra seat.
    cases = (
        _cases(CaseCategory.NORMAL, 10, prefix="n")
        + _cases(CaseCategory.AMBIGUOUS, 10, prefix="a")
        + _cases(CaseCategory.UNSUPPORTED, 10, prefix="u")
    )

    sample = select_stratified_sample(cases, sample_size=10)

    assert sum(1 for c in sample if c.category is CaseCategory.NORMAL) == 4
    assert sum(1 for c in sample if c.category is CaseCategory.AMBIGUOUS) == 3
    assert sum(1 for c in sample if c.category is CaseCategory.UNSUPPORTED) == 3


def test_the_sample_is_deterministic_across_calls() -> None:
    cases = _cases(CaseCategory.NORMAL, 20, prefix="n") + _cases(
        CaseCategory.ADVERSARIAL, 15, prefix="a"
    )

    first = select_stratified_sample(cases, sample_size=10)
    second = select_stratified_sample(cases, sample_size=10)

    assert [c.case_id for c in first] == [c.case_id for c in second]


def test_within_a_category_the_first_cases_in_order_are_taken() -> None:
    cases = _cases(CaseCategory.NORMAL, 5, prefix="n")

    sample = select_stratified_sample(cases, sample_size=3)

    assert [c.case_id for c in sample] == ["n-00", "n-01", "n-02"]


def test_the_real_golden_set_allocates_fifty_across_all_six_categories() -> None:
    """Regression check against the real 135-case mix (41/32/22/17/13/10 by category): locks in
    the exact allocation a reviewer can hand-verify against the largest-remainder method."""
    sample = select_stratified_sample(ALL_CASES, sample_size=50)

    assert len(sample) == 50
    by_category = {
        category: sum(1 for c in sample if c.category is category) for category in CaseCategory
    }
    assert by_category == {
        CaseCategory.NORMAL: 15,
        CaseCategory.AMBIGUOUS: 6,
        CaseCategory.UNSUPPORTED: 5,
        CaseCategory.HUMAN_REQUIRED: 8,
        CaseCategory.MULTILINGUAL: 4,
        CaseCategory.ADVERSARIAL: 12,
    }


# -----------------------------------------------------------------------------
# build_h4_rows
# -----------------------------------------------------------------------------


def _case_result(**overrides: Any) -> CaseResult:
    defaults: dict[str, Any] = {
        "case_id": "c1",
        "is_adversarial": False,
        "expected_escalation": False,
        "observed_escalation": False,
        "automation_attempted": True,
        "correct_outcome": True,
    }
    return CaseResult(**{**defaults, **overrides})


def test_build_h4_rows_fills_the_captured_columns_when_present() -> None:
    case = _case(case_id="c1", user_turns=("Primero.", "Segundo."))
    result = _case_result(case_id="c1", reply_text="La respuesta.", facts_and_sources="Los hechos.")

    rows = build_h4_rows((case,), {"c1": result})

    assert rows == [
        {
            "case_id": "c1",
            "language": "es",
            "category": "normal",
            "user_turns": "Primero. | Segundo.",
            "system_replies": "La respuesta.",
            "facts_and_sources": "Los hechos.",
        }
    ]


def test_build_h4_rows_renders_a_missing_result_as_blank_columns() -> None:
    case = _case(case_id="c1")

    rows = build_h4_rows((case,), {})

    assert rows[0]["system_replies"] == ""
    assert rows[0]["facts_and_sources"] == ""


def test_build_h4_rows_renders_an_uncaptured_result_as_blank_columns() -> None:
    """A result exists (the case ran and scored) but capture itself missed it — same blank
    rendering as no result at all, never a fabricated placeholder."""
    case = _case(case_id="c1")
    result = _case_result(case_id="c1")  # reply_text/facts_and_sources both default to None

    rows = build_h4_rows((case,), {"c1": result})

    assert rows[0]["system_replies"] == ""
    assert rows[0]["facts_and_sources"] == ""


# -----------------------------------------------------------------------------
# write_h4_case_sheet
# -----------------------------------------------------------------------------


def test_write_h4_case_sheet_writes_the_six_columns_in_order(tmp_path: Path) -> None:
    rows = [
        {
            "case_id": "c1",
            "language": "es",
            "category": "normal",
            "user_turns": "Primero.",
            "system_replies": "La respuesta.",
            "facts_and_sources": "Los hechos.",
        }
    ]
    target = tmp_path / "human-tasks" / "H4-case-sheet.csv"

    write_h4_case_sheet(rows, target)

    with target.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == [
            "case_id",
            "language",
            "category",
            "user_turns",
            "system_replies",
            "facts_and_sources",
        ]
        assert list(reader) == rows
