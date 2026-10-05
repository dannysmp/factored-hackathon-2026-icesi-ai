"""
Rater Case Sheet Export
=======================

Overview
--------
Produces ``H4-case-sheet.csv``, the sheet the judge rubric asks two human raters to double-score:
a stratified sample of the golden set (50 cases by default) in which each row's
``system_replies`` and ``facts_and_sources`` columns are filled from a captured run of the proposed
system (``CaseResult.reply_text`` and ``CaseResult.facts_and_sources``, produced when the runner
is given ``capture_transcripts=True``; see ``evals.facts.attach_masked_transcript``). The sample in
``evals.golden.judge_validation_sample`` is a separate synthetic fixture for the agreement
computation; it plays no part in this sheet.

Scope
-----
In: ``select_stratified_sample`` (deterministic, proportional to each category's share of the
cases given, no randomness), ``build_h4_rows`` (the golden set's static columns plus the two
columns a captured run fills) and ``write_h4_case_sheet`` (the file itself).
Out: running or capturing anything, and distributing the file. The caller runs the proposed system
with ``capture_transcripts=True`` first (``evals.runner.runner``) and passes this module the
sample and the resulting results.

Design Principles
-----------------
- **Proportional, deterministic, reproducible.** Largest-remainder (Hamilton) allocation across the
  six ``CaseCategory`` values, from each category's share of the cases given; a tie in the
  remainder breaks by ``CaseCategory`` declaration order, never at random, so the same cases
  always yield the same sample.
- **A missed capture is a blank cell, never a placeholder.** A case with no ``reply_text`` or
  ``facts_and_sources`` (see the Limitations of ``evals.runner.runner``: a declared policy section
  that failed to resolve) renders those two columns empty. The rubric says not to start rating
  until both are filled, and an empty cell is visibly incomplete where guessed text is not.
- **``user_turns`` needs no captured run.** It is the golden set's own field, joined with `` | ``
  exactly as ``evals.golden.case_sheet`` joins it, so the two sheets read alike.

Runtime Contract
-----------------
``select_stratified_sample(cases, *, sample_size=50) -> tuple[Case, ...]``.
``build_h4_rows(cases, results) -> list[dict[str, str]]``, ``results`` keyed by ``case_id``.
``write_h4_case_sheet(rows, path) -> None``.

Limitations
-----------
The allocation assumes ``cases`` is not empty, ``sample_size <= len(cases)`` and that no
category's allocation exceeds its own case count; none of this is guarded, and an empty ``cases``
raises ``ZeroDivisionError``. This module does not check that ``results`` came from a run of the
cases it is given; that is the caller's responsibility.
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

# The sheet's columns, in file order.
_COLUMNS = ("case_id", "language", "category", "user_turns", "system_replies", "facts_and_sources")


def select_stratified_sample(cases: Sequence[Case], *, sample_size: int = 50) -> tuple[Case, ...]:
    """The proportional, deterministic sample the human raters double-score.

    Each ``CaseCategory``'s share of ``sample_size`` is its share of ``cases`` rounded down, with
    the leftover seats given to the categories with the largest fractional remainder (largest-
    remainder/Hamilton apportionment); a tie breaks by ``CaseCategory`` declaration order. Within a
    category the first cases in ``cases`` order are taken, so identical input always yields the
    identical sample.

    Parameters
    ----------
    cases : Sequence[Case]
        The full set to sample from; must not be empty.
    sample_size : int
        How many cases to select; at most ``len(cases)``.

    Returns
    -------
    tuple[Case, ...]
        The sample, grouped by category in ``CaseCategory`` order.
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
    """One sheet row per case, in ``cases`` order.

    A case with no entry in ``results``, or whose result carries no captured transcript, renders
    its two run-dependent columns (``system_replies``, ``facts_and_sources``) empty.

    Parameters
    ----------
    cases : Sequence[Case]
        The sampled cases.
    results : Mapping[str, CaseResult]
        Captured results keyed by ``case_id``.

    Returns
    -------
    list[dict[str, str]]
        One dict per case, keyed by the six sheet columns.
    """
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
    """Write ``rows`` as the six-column CSV at ``path``, creating its parent directories.

    The file is UTF-8, uses Unix line endings and starts with a header row.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(buffer.getvalue(), encoding="utf-8")
