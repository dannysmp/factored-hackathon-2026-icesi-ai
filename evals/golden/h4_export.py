"""
Case Sheet Export
=================

Overview
--------
Produces the real ``H4-case-sheet.csv`` the judge rubric asks two
human raters to double-score: a stratified 50-case sample of the golden set, with each row's
``system_replies``/``facts_and_sources`` filled from a real, captured run
(``CaseResult.reply_text``/``facts_and_sources``, ``evals.facts.attach_masked_transcript``'s own
opt-in output). Fills, for the two blank columns, whatever synthetic stand-in a caller was using
before a real run existed to capture them (``evals.golden.judge_validation_sample`` — a different
fixture, for the agreement computation, not for this sheet).

Scope
-----
In: ``select_stratified_sample`` (deterministic, proportional to the 135-case mix table, no
randomness), ``build_h4_rows`` (the golden set's own static columns plus a captured run's two
blank ones), ``write_h4_case_sheet`` (the file itself).
Out: running anything, capturing anything, or choosing where the file is distributed once
written — the caller runs the proposed system with ``capture_transcripts=True`` first
(``evals.runner.runner``) and passes this module only the sample and the resulting results.

Design Principles
-----------------
- **Proportional, deterministic, reproducible.** Largest-remainder (Hamilton) allocation across the
  six ``CaseCategory`` values, from each category's own share of the full golden set; a tie in the
  remainder breaks by ``CaseCategory``'s own declaration order, never at random — re-running this
  against the same golden set always selects the identical 50 case ids.
- **A missed capture renders as a blank cell, never as a fabricated placeholder.** A case the
  captured run has no ``reply_text``/``facts_and_sources`` for (``evals.runner.runner``'s own
  Limitations (capture): a declared policy section that failed to resolve) renders those two
  columns empty, matching the rubric's own instruction not to start rating until both columns are
  filled — an empty cell is visibly incomplete, unlike guessed text.
- **``user_turns`` needs no captured run at all.** The golden set's own static field, joined the
  same way ``evals.golden.case_sheet`` already joins it for its own generated files (`` | ``), so
  the two case-sheet-shaped files this project produces stay visually consistent.

Runtime Contract
-----------------
``select_stratified_sample(cases, *, sample_size=50) -> tuple[Case, ...]``.
``build_h4_rows(cases, results) -> list[dict[str, str]]``, ``results`` keyed by ``case_id``.
``write_h4_case_sheet(rows, path) -> None``.

Limitations
-----------
The largest-remainder allocation assumes ``sample_size <= len(cases)`` and that no category's
allocation could exceed its own count; both hold for every sample size this module is ever asked
for against the 135-case golden set (50 of 135), and this module does not guard the general case
further. This module never validates that ``results`` actually came from a run of exactly the
cases it is given — the caller's own responsibility, the same trust boundary
``evals.golden.case_sheet``'s own loader-free design already accepts for `Case` objects generally.
"""

from __future__ import annotations

# Standard libraries
import csv
import io
from collections.abc import Mapping, Sequence
from pathlib import Path

# Local modules
from evals.metrics import CaseResult
from evals.models import Case, CaseCategory

_COLUMNS = ("case_id", "language", "category", "user_turns", "system_replies", "facts_and_sources")


def select_stratified_sample(cases: Sequence[Case], *, sample_size: int = 50) -> tuple[Case, ...]:
    """The proportional, deterministic sample the human raters double-score.

    Each ``CaseCategory``'s share of ``sample_size`` is its share of ``cases`` rounded down, with
    the leftover seats given to the categories with the largest fractional remainder (largest-
    remainder/Hamilton apportionment); a tie breaks by ``CaseCategory``'s own declaration order.
    Within a category, the first cases in ``cases``'s own order are taken — deterministic, no
    randomness, so this always returns the identical 50 case ids for the identical input.
    """
    total = len(cases)
    by_category: dict[CaseCategory, list[Case]] = {category: [] for category in CaseCategory}
    for case in cases:
        by_category[case.category].append(case)

    exact = {category: len(group) * sample_size / total for category, group in by_category.items()}
    allocation = {category: int(value) for category, value in exact.items()}
    seats_left = sample_size - sum(allocation.values())
    by_largest_remainder = sorted(
        by_category, key=lambda category: exact[category] - allocation[category], reverse=True
    )
    for category in by_largest_remainder[:seats_left]:
        allocation[category] += 1

    selected: list[Case] = []
    for category in CaseCategory:
        selected.extend(by_category[category][: allocation[category]])
    return tuple(selected)


def build_h4_rows(cases: Sequence[Case], results: Mapping[str, CaseResult]) -> list[dict[str, str]]:
    """One row per case, in ``cases``'s own order; a case the captured run has no result for (or
    whose result carries no captured transcript) renders its two run-dependent columns empty."""
    rows = []
    for case in cases:
        result = results.get(case.case_id)
        rows.append(
            {
                "case_id": case.case_id,
                "language": case.lang,
                "category": case.category.value,
                "user_turns": " | ".join(case.user_turns),
                "system_replies": (result.reply_text if result and result.reply_text else ""),
                "facts_and_sources": (
                    result.facts_and_sources if result and result.facts_and_sources else ""
                ),
            }
        )
    return rows


def write_h4_case_sheet(rows: Sequence[Mapping[str, str]], path: Path) -> None:
    """Write ``rows`` as ``H4-case-sheet.csv``'s own six columns, creating ``path``'s parents."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(buffer.getvalue(), encoding="utf-8")
