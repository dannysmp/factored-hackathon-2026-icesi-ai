"""
Golden Set Case Sheet Tests
============================

Component: ``evals.golden.case_sheet``. Uses ``tmp_path`` for the write/check round trip, so the
committed sheet under ``evals/golden/`` is never touched by the suite; the drift check against
the real committed file is exercised separately, against the actual package directory.
"""

from __future__ import annotations

# Standard libraries
import csv
import io
from pathlib import Path

# Third-party libraries
import pytest

# Local modules
from evals.golden import case_sheet
from evals.golden.case_sheet import (
    ALL_CASES,
    DEFAULT_DIRECTORY,
    check_case_sheet,
    main,
    render_case_sheet,
    write_case_sheet,
)


def test_render_produces_one_row_per_case() -> None:
    text = render_case_sheet(ALL_CASES)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == len(ALL_CASES)


def test_render_is_deterministic() -> None:
    assert render_case_sheet(ALL_CASES) == render_case_sheet(ALL_CASES)


def test_row_fields_match_the_case(tmp_path: Path) -> None:
    text = render_case_sheet(ALL_CASES)
    rows = list(csv.DictReader(io.StringIO(text)))
    first_case, first_row = ALL_CASES[0], rows[0]
    assert first_row["case_id"] == first_case.case_id
    assert first_row["lang"] == first_case.lang
    assert first_row["user_turns"] == " | ".join(first_case.user_turns)


def test_write_then_check_round_trip(tmp_path: Path) -> None:
    assert check_case_sheet(tmp_path) == ["missing: case_sheet.csv"]
    changed = write_case_sheet(tmp_path)
    assert changed == ["case_sheet.csv"]
    assert check_case_sheet(tmp_path) == []


def test_write_is_idempotent(tmp_path: Path) -> None:
    write_case_sheet(tmp_path)
    assert write_case_sheet(tmp_path) == []


def test_committed_case_sheet_has_no_drift() -> None:
    # The file actually committed under evals/golden/ must be exactly what the cases generate.
    assert check_case_sheet(DEFAULT_DIRECTORY) == []


def test_check_reports_stale_when_content_differs(tmp_path: Path) -> None:
    (tmp_path / "case_sheet.csv").write_text("stale content", encoding="utf-8")
    assert check_case_sheet(tmp_path) == ["stale: case_sheet.csv"]


def test_main_writes_the_sheet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(case_sheet, "DEFAULT_DIRECTORY", tmp_path)
    assert main([]) == 0
    assert (tmp_path / "case_sheet.csv").read_text(encoding="utf-8") == render_case_sheet(
        ALL_CASES
    )
    # A second run finds nothing to write, exercising the idempotent branch too.
    assert main([]) == 0


def test_main_check_fails_until_the_sheet_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(case_sheet, "DEFAULT_DIRECTORY", tmp_path)
    assert main(["--check"]) == 1
    write_case_sheet(tmp_path)
    assert main(["--check"]) == 0


def test_main_check_fails_on_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(case_sheet, "DEFAULT_DIRECTORY", tmp_path)
    write_case_sheet(tmp_path)
    (tmp_path / "case_sheet.csv").write_text("stale content", encoding="utf-8")
    assert main(["--check"]) == 1
