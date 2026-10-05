"""
Golden Set Case Files
======================

Overview
--------
Renders each category group's authored cases as its own CSV file under ``evals/golden/cases/``,
or checks that the committed files are exactly what the cases would generate. The check is what
stops the files a language reviewer reads from drifting away from the cases the harness actually
runs. Nothing at runtime reads these files: the harness consumes `Case` objects directly from the
category modules; the files exist only for a human reader, so no loader reconstructs them into
records. `Case.__post_init__` validates each record when its module is imported.

Scope
-----
In: reading the category modules' `CASES` tuples, rendering or comparing the per-category CSV
files, the command line ``python -m evals.golden.case_sheet [--check]``.
Out: the cases themselves (one module per category); running or scoring a case.

Design Principles
-------------------
- **One file per category group, never a shared one.** Each category group owns its own file
  (`evals/golden/cases/<category>.csv`), so no two category groups can conflict on the same
  generated file. Only this module itself is shared, and adding a category only ever needs one
  import and one `CATEGORY_CASES` entry, never a change to the render or check functions.
- **`ALL_CASES` follows the category declaration order, not import order.** It iterates
  `CaseCategory` (normal, ambiguous, unsupported, human-required, multilingual, adversarial) and
  looks each one up in `CATEGORY_CASES`, so the order never depends on how the modules were
  imported.
- **A pure render, a write that skips unchanged files, and a check.** This follows
  `app.domain.policy.corpus` and `pipelines.policy_corpus`, including stray-file detection like
  `pipelines.policy_corpus.check_corpus`. A test calls the check, so a changed category whose file
  was not regenerated fails the suite.
- One row per case, one case per row: `user_turns` join with " | " so a file stays one line per
  case for a reviewer scanning it, and columns are stable and named, never positional.

Runtime Contract
-----------------
``CATEGORY_CASES``: every category module's `CASES`, keyed by its `CaseCategory`. ``ALL_CASES``:
every case, in `CaseCategory`'s declared order. ``render_case_sheet(cases) -> str``.
``write_case_sheet(directory) -> list[str]`` returns the relative paths that changed.
``check_case_sheet(directory) -> list[str]`` returns what is missing, differs, or is stray.
``main(argv) -> int``: 0 on success, 1 when ``--check`` finds drift.

Limitations
-----------
The command line only renders to, or checks, the default directory. A `CaseCategory` with no
entry in `CATEGORY_CASES` contributes no cases and no file.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command line
import csv  # Renders each category's cases as a CSV file
import io  # In-memory buffer csv.DictWriter writes into before comparing or saving
import logging  # Progress events
import os  # Atomic file replacement
from collections.abc import Sequence  # Type of a case list
from pathlib import Path  # Locations of the rendered files

# Local modules
from evals.golden.adversarial import CASES as ADVERSARIAL_CASES  # Category group
from evals.golden.ambiguous import CASES as AMBIGUOUS_CASES  # Category group
from evals.golden.human_required import CASES as HUMAN_REQUIRED_CASES  # Category group
from evals.golden.multilingual import CASES as MULTILINGUAL_CASES  # Category group
from evals.golden.normal import CASES as NORMAL_CASES  # Category group
from evals.golden.unsupported import CASES as UNSUPPORTED_CASES  # Category group
from evals.models import Case, CaseCategory  # The record shape and its category vocabulary

logger = logging.getLogger(__name__)

DEFAULT_DIRECTORY = Path(__file__).parent / "cases"

#: Every category module's cases, keyed by the `CaseCategory` its own CSV is written under.
#: Adding a category is one import and one entry here, never a change to the functions below.
CATEGORY_CASES: dict[CaseCategory, tuple[Case, ...]] = {
    CaseCategory.HUMAN_REQUIRED: HUMAN_REQUIRED_CASES,
    CaseCategory.NORMAL: NORMAL_CASES,
    CaseCategory.AMBIGUOUS: AMBIGUOUS_CASES,
    CaseCategory.UNSUPPORTED: UNSUPPORTED_CASES,
    CaseCategory.MULTILINGUAL: MULTILINGUAL_CASES,
    CaseCategory.ADVERSARIAL: ADVERSARIAL_CASES,
}

#: Every authored case, in `CaseCategory`'s declared order, not the order the categories are
#: imported in.
ALL_CASES: tuple[Case, ...] = tuple(
    case for category in CaseCategory for case in CATEGORY_CASES.get(category, ())
)

# The case-sheet columns, in file order: the stable, named header of every category file.
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
    "injected_failure",
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
        "injected_failure": (
            f"{case.injected_failure.tool.value}:{case.injected_failure.cause}"
            f":retryable={str(case.injected_failure.retryable).lower()}"
            if case.injected_failure
            else ""
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


def _expected_files() -> dict[str, str]:
    """Every category's expected relative filename mapped to its rendered content."""
    return {
        f"{category.value}.csv": render_case_sheet(cases)
        for category, cases in CATEGORY_CASES.items()
    }


def write_case_sheet(directory: Path = DEFAULT_DIRECTORY) -> list[str]:
    """Write every category's CSV under ``directory``; return the relative paths that changed."""
    changed = []
    for relative, text in _expected_files().items():
        target = directory / relative
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".csv.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
        changed.append(relative)
    return changed


def check_case_sheet(directory: Path = DEFAULT_DIRECTORY) -> list[str]:
    """Relative paths under ``directory`` that are missing, stale or not generated.

    Hidden files are ignored. An empty list means the directory matches the cases exactly.
    """
    expected = _expected_files()
    drift = []
    for relative, text in expected.items():
        target = directory / relative
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            drift.append(relative)
    if directory.is_dir():
        # Hidden files (``.DS_Store`` and the like) are tooling debris, not case-sheet content.
        present = {
            relative.as_posix()
            for relative in (path.relative_to(directory) for path in directory.rglob("*"))
            if (directory / relative).is_file()
            and not any(part.startswith(".") for part in relative.parts)
        }
        drift.extend(sorted(present - set(expected)))
    return drift


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point: ``python -m evals.golden.case_sheet [--check]``.

    Without ``--check`` it writes the category files; with it, it only reports drift. Returns ``1``
    when ``--check`` finds a missing, stale or stray file, otherwise ``0``.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail instead of writing when a file is stale"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.check:
        drift = check_case_sheet(DEFAULT_DIRECTORY)
        for relative in drift:
            logger.error("case_sheet_drift file=%s", relative)
        if drift:
            return 1
        logger.info("case_sheet_current")
        return 0

    for relative in write_case_sheet(DEFAULT_DIRECTORY):
        logger.info("case_sheet_written file=%s", relative)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
