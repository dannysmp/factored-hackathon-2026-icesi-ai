"""
Golden Set Case Files Tests
============================

Component: ``evals.golden.case_sheet``. Uses ``tmp_path`` for the write/check round trip, so the
committed files under ``evals/golden/cases/`` are never touched by the suite; the drift check
against the real committed files is exercised separately, against the actual package directory.
"""

from __future__ import annotations

# Standard libraries
import csv
import io
from pathlib import Path

# Third-party libraries
import pytest

# Local modules
from contracts.service_v1.envelope import Intent
from contracts.service_v1.tools import Tool
from evals.golden import case_sheet
from evals.golden.case_sheet import (
    ALL_CASES,
    CATEGORY_CASES,
    DEFAULT_DIRECTORY,
    check_case_sheet,
    main,
    render_case_sheet,
    write_case_sheet,
)
from evals.models import Case, CaseCategory, InjectedToolFailure, SafeBehavior


def test_render_produces_one_row_per_case() -> None:
    text = render_case_sheet(ALL_CASES)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == len(ALL_CASES)


def test_render_is_deterministic() -> None:
    assert render_case_sheet(ALL_CASES) == render_case_sheet(ALL_CASES)


def test_row_fields_match_the_case() -> None:
    text = render_case_sheet(ALL_CASES)
    rows = list(csv.DictReader(io.StringIO(text)))
    first_case, first_row = ALL_CASES[0], rows[0]
    assert first_row["case_id"] == first_case.case_id
    assert first_row["lang"] == first_case.lang
    assert first_row["user_turns"] == " | ".join(first_case.user_turns)


def _injected_failure_case(*, retryable: bool) -> Case:
    return Case(
        case_id=f"injected-failure-retryable-{retryable}",
        category=CaseCategory.ADVERSARIAL,
        lang="es",
        provenance="injected",
        seed_ref="eval_bank:UNUSED",
        user_turns=("¿Cuáles son mis transacciones?",),
        expected_intent=Intent.HANDOFF,
        expected_safe_behavior=SafeBehavior.HANDOFF,
        injected_failure=InjectedToolFailure(
            tool=Tool.LIST_TRANSACTIONS, cause="error", retryable=retryable
        ),
        description="Unused by this test.",
    )


def _rendered_injected_failure(case: Case) -> str:
    row = next(csv.DictReader(io.StringIO(render_case_sheet((case,)))))
    return row["injected_failure"]


def test_the_injected_failure_column_states_retryable() -> None:
    # A dropped `retryable` would render the two cases below identically, silently losing exactly
    # the distinction a case is authored to test.
    assert (
        _rendered_injected_failure(_injected_failure_case(retryable=True))
        == "list_transactions:error:retryable=true"
    )
    assert (
        _rendered_injected_failure(_injected_failure_case(retryable=False))
        == "list_transactions:error:retryable=false"
    )


def test_all_cases_is_ordered_by_category_declaration_not_delivery() -> None:
    # CaseCategory's own declared order is the mix table's row order; a case's position in
    # ALL_CASES must follow that, not whichever order category-group pull requests landed in.
    order = [category for category in CaseCategory if category in CATEGORY_CASES]
    seen_order = []
    for case in ALL_CASES:
        if case.category not in seen_order:
            seen_order.append(case.category)
    assert seen_order == order


def test_all_cases_has_no_duplicate_case_ids() -> None:
    ids = [case.case_id for case in ALL_CASES]
    assert len(ids) == len(set(ids))


def test_all_cases_has_no_duplicate_ops_seed_refs() -> None:
    # eval_bank refs may legitimately repeat (evals.golden.adversarial shares its four scenarios
    # across ten cases; see that module's own test for the exact count), but every ops_seed ref
    # anchors exactly one case across the whole golden set.
    refs = [case.seed_ref for case in ALL_CASES if case.seed_ref.startswith("ops_seed:")]
    assert len(refs) == len(set(refs))


def test_write_then_check_round_trip(tmp_path: Path) -> None:
    expected_missing = sorted(f"{category.value}.csv" for category in CATEGORY_CASES)
    assert sorted(check_case_sheet(tmp_path)) == expected_missing
    changed = write_case_sheet(tmp_path)
    assert sorted(changed) == expected_missing
    assert check_case_sheet(tmp_path) == []


def test_write_is_idempotent(tmp_path: Path) -> None:
    write_case_sheet(tmp_path)
    assert write_case_sheet(tmp_path) == []


def test_check_reports_a_stray_file(tmp_path: Path) -> None:
    write_case_sheet(tmp_path)
    (tmp_path / "not_a_category.csv").write_text("stray", encoding="utf-8")
    assert check_case_sheet(tmp_path) == ["not_a_category.csv"]


def test_check_reports_drift_when_content_differs(tmp_path: Path) -> None:
    write_case_sheet(tmp_path)
    first_file = f"{next(iter(CATEGORY_CASES)).value}.csv"
    (tmp_path / first_file).write_text("stale content", encoding="utf-8")
    assert check_case_sheet(tmp_path) == [first_file]


def test_check_on_a_nonexistent_directory_reports_missing_without_crashing(tmp_path: Path) -> None:
    missing_directory = tmp_path / "does-not-exist-yet"
    expected = sorted(f"{category.value}.csv" for category in CATEGORY_CASES)
    assert sorted(check_case_sheet(missing_directory)) == expected


def test_main_writes_every_category(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(case_sheet, "DEFAULT_DIRECTORY", tmp_path)
    assert main([]) == 0
    for category, cases in CATEGORY_CASES.items():
        content = (tmp_path / f"{category.value}.csv").read_text(encoding="utf-8")
        assert content == render_case_sheet(cases)
    # A second run finds nothing to write, exercising the idempotent branch too.
    assert main([]) == 0


def test_main_check_fails_until_the_files_are_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(case_sheet, "DEFAULT_DIRECTORY", tmp_path)
    assert main(["--check"]) == 1
    write_case_sheet(tmp_path)
    assert main(["--check"]) == 0


def test_main_check_fails_on_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(case_sheet, "DEFAULT_DIRECTORY", tmp_path)
    write_case_sheet(tmp_path)
    first_file = f"{next(iter(CATEGORY_CASES)).value}.csv"
    (tmp_path / first_file).write_text("stale content", encoding="utf-8")
    assert main(["--check"]) == 1


def test_committed_case_files_have_no_drift() -> None:
    # The files actually committed under evals/golden/cases/ must be exactly what the cases
    # generate — the check CI relies on.
    assert check_case_sheet(DEFAULT_DIRECTORY) == []
