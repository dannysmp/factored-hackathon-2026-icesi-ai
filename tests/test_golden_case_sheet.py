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
import re
import subprocess
import sys
from pathlib import Path

# Third-party libraries
import pytest

# Local modules
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
from evals.models import CaseCategory

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOG_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} (INFO|ERROR) ")


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


def _run_case_sheet_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Runs the CLI as a real, separate process against ``tmp_path``, not this test's own.

    ``logging.basicConfig`` is a documented no-op once a handler is already attached to the root
    logger, which pytest's own capture machinery does before any test body runs; ``caplog`` reads
    records, not the rendered text, so neither can observe the CLI's own format string. Only a
    genuinely separate process, with its own untouched root logger, does.
    """
    script = (
        "from pathlib import Path\n"
        "from evals.golden import case_sheet\n"
        f"case_sheet.DEFAULT_DIRECTORY = Path({str(tmp_path)!r})\n"
        f"raise SystemExit(case_sheet.main({list(args)!r}))\n"
    )
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted interpreter path
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_write_logs_are_timestamped_and_leveled(tmp_path: Path) -> None:
    """The format string must carry a timestamp and level, matching `pipelines.policy_corpus`'s
    own `main()`, and the write path must log a line for every file it actually writes — checked
    against a real, observed process output, not a read of the source alone."""
    result = _run_case_sheet_cli(tmp_path)
    assert result.returncode == 0
    written_lines = [line for line in result.stderr.splitlines() if "case_sheet_written" in line]
    assert len(written_lines) == len(CATEGORY_CASES)
    for line in written_lines:
        assert _LOG_LINE.match(line), f"missing timestamp/level: {line!r}"


def test_cli_check_logs_are_timestamped_and_leveled(tmp_path: Path) -> None:
    write_case_sheet(tmp_path)
    result = _run_case_sheet_cli(tmp_path, "--check")
    assert result.returncode == 0
    current_lines = [line for line in result.stderr.splitlines() if "case_sheet_current" in line]
    assert len(current_lines) == 1
    assert _LOG_LINE.match(current_lines[0])


def test_cli_write_logs_nothing_on_a_second_idempotent_run(tmp_path: Path) -> None:
    """Zero lines on a clean directory is the correct, observed output — not a logging defect and
    not an artifact of a broken logger."""
    _run_case_sheet_cli(tmp_path)
    result = _run_case_sheet_cli(tmp_path)
    assert result.returncode == 0
    assert "case_sheet_written" not in result.stderr
