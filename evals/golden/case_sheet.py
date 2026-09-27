"""
Golden Set Case Sheet
=====================

Overview
--------
Renders the golden set's authored cases as a CSV case sheet (``evals/golden/case_sheet.csv``), or
checks that the committed file is exactly what the cases would generate. The check is what stops
the sheet a language reviewer reads from drifting away from the cases the harness actually runs.

Scope
-----
In: reading the category modules' `CASES` tuples, rendering or comparing the one CSV file, the
command line ``python -m evals.golden.case_sheet [--check]``.
Out: the cases themselves (one module per category); running or scoring a case.

Design Principles
-------------------
- Mirrors `app.domain.policy.corpus` and `pipelines.policy_corpus`: a pure render function, a
  write step that only touches the file when its content changed, and a check step a test and CI
  both call so an added category that forgets to regenerate the sheet cannot be merged.
- One row per case, one case per row: `user_turns` join with " | " so the sheet stays one line per
  case for a reviewer scanning it, and columns are stable and named, never positional.
- `ALL_CASES` concatenates every category module in the golden set's mix-table order; adding a
  category is one import and one line here, never a change to the render function.

Runtime Contract
-----------------
``render_case_sheet(cases) -> str``. ``write_case_sheet(directory) -> list[str]`` returns the
files that changed (empty when the sheet was already correct). ``check_case_sheet(directory) ->
list[str]`` returns what differs, is missing, or is stray. ``main(argv) -> int``: 0 on success, 1
when ``--check`` finds drift.

Limitations
-----------
Only the human-required, normal, ambiguous, unsupported and multilingual categories exist as of
this slice; the sheet holds 103 of the golden set's 135 cases until the adversarial category
lands.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command line
import csv
import io
import logging  # Progress events
import os  # Atomic file replacement
from collections.abc import Sequence
from pathlib import Path

# Local modules
from evals.golden.ambiguous import CASES as AMBIGUOUS_CASES  # Category group 3
from evals.golden.human_required import CASES as HUMAN_REQUIRED_CASES  # Category group 1
from evals.golden.multilingual import CASES as MULTILINGUAL_CASES  # Category group 5
from evals.golden.normal import CASES as NORMAL_CASES  # Category group 2
from evals.golden.unsupported import CASES as UNSUPPORTED_CASES  # Category group 4
from evals.models import Case  # The record shape rendered as a row

logger = logging.getLogger(__name__)

DEFAULT_DIRECTORY = Path(__file__).parent
SHEET_FILENAME = "case_sheet.csv"

#: Every authored case, in delivery order (each category group's own PR), not the mix table's
#: row order (`plan/docs/evaluation-plan.md`) — human-required landed first, then normal, then
#: ambiguous, then unsupported, then multilingual.
ALL_CASES: tuple[Case, ...] = (
    HUMAN_REQUIRED_CASES + NORMAL_CASES + AMBIGUOUS_CASES + UNSUPPORTED_CASES + MULTILINGUAL_CASES
)

_COLUMNS = (
    "case_id",
    "category",
    "lang",
    "provenance",
    "seed_ref",
    "user_turns",
    "expected_intent",
    "expected_reason_code",
    "expected_safe_behavior",
    "description",
)


def _row(case: Case) -> dict[str, str]:
    """One case as a case-sheet row; every optional field renders as an empty string, not `None`."""
    return {
        "case_id": case.case_id,
        "category": case.category.value,
        "lang": case.lang,
        "provenance": case.provenance,
        "seed_ref": case.seed_ref,
        "user_turns": " | ".join(case.user_turns),
        "expected_intent": case.expected_intent.value,
        "expected_reason_code": (
            case.expected_reason_code.value if case.expected_reason_code else ""
        ),
        "expected_safe_behavior": (
            case.expected_safe_behavior.value if case.expected_safe_behavior else ""
        ),
        "description": case.description,
    }


def render_case_sheet(cases: Sequence[Case]) -> str:
    """The cases as CSV text, one row per case, in the given order."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for case in cases:
        writer.writerow(_row(case))
    return buffer.getvalue()


def write_case_sheet(directory: Path = DEFAULT_DIRECTORY) -> list[str]:
    """Write the case sheet under ``directory``; return the relative paths that changed."""
    text = render_case_sheet(ALL_CASES)
    target = directory / SHEET_FILENAME
    if target.is_file() and target.read_text(encoding="utf-8") == text:
        return []
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".csv.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)
    return [SHEET_FILENAME]


def check_case_sheet(directory: Path = DEFAULT_DIRECTORY) -> list[str]:
    """What differs from, is missing from, or is stray in ``directory``; empty means clean."""
    text = render_case_sheet(ALL_CASES)
    target = directory / SHEET_FILENAME
    if not target.is_file():
        return [f"missing: {SHEET_FILENAME}"]
    if target.read_text(encoding="utf-8") != text:
        return [f"stale: {SHEET_FILENAME}"]
    return []


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m evals.golden.case_sheet [--check]``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail instead of writing when the sheet is stale"
    )
    args = parser.parse_args(argv)

    if args.check:
        drift = check_case_sheet(DEFAULT_DIRECTORY)
        for line in drift:
            logger.error(line)
        return 1 if drift else 0

    for changed in write_case_sheet(DEFAULT_DIRECTORY):
        logger.info("wrote %s", changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
