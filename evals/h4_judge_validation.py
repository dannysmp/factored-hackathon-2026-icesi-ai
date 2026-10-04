"""
H4 Judge Validation — Real Sample
====================================

Overview
--------
Turns the two returned H4 case sheets (``H4-case-sheet-Rater1.csv``, ``H4-case-sheet-Rater2.csv``)
into ``evals.judge_validation.RaterScore`` tuples, scores the same 50 cases with the real automated
judge, computes the agreement ``evals.judge_validation.compute_agreement`` already implements and
the pair counts, weighted kappa and gap direction ``compute_detail`` adds, writes every case's
scores to a CSV, and patches the committed ``reports/evaluation.md`` so its judge-validation
section (and the one limitations bullet that names it as pending) reflect the real,
human-provenance sample instead of the synthetic placeholder.

Scope
-----
In: parsing the returned sheets' columns, checking the two sheets describe the same prepared
packet, calling the real judge once per case over the sheet's own ``system_replies``/
``facts_and_sources`` (already captured when the sheets were prepared — this module never re-runs
a system to get them), and patching the report.
Out: computing agreement itself (``evals.judge_validation``, unchanged); the judge's own scoring
call (``evals.judge.LlmJudge``, unchanged); the written analysis of where raters and judge
disagree, which stays a person's job; running the systems that produced
``system_replies``/``facts_and_sources`` in the first place.

Design Principles
-----------------
- **The two rater sheets are the only source of ``system_replies``/``facts_and_sources``.** Those
  columns are filled once, when the sheets are prepared, from a real run's captured output — not
  re-derived here, and not assumed identical across every row without checking (a mismatch
  between the two returned sheets on any shared column means the sheets were not built from the
  same prepared packet, and this module refuses rather than silently trusting one file over the
  other).
- **Every refusal happens before the first paid judge call.** Sheet integrity, rater roles and
  the report's shape are all checked up front.
- **One judge call per case, never a batch call.** ``evals.judge.LlmJudge.score`` is already built
  for exactly one transcript at a time; this module does not add a second call shape for a sample
  this small (50 cases).
- **A row with no ``clarification`` score (the rubric's own ``NA`` convention) becomes ``None``,
  never ``0``.** ``evals.judge_validation``'s own exclusion-from-the-denominator rule depends on
  that distinction, the same rule the synthetic placeholder fixture already exercises.
- **The report is patched, never rebuilt from scratch.** Rebuilding ``EvaluationReport`` fully
  would mean re-running every system for real money, just to change the one section that actually
  depends on human data; patching only the judge-validation section and the one limitations bullet
  that names it as pending keeps every other section (versions, headline metrics, the failure
  gallery) exactly as the real run already produced them. The patch targets the exact text
  ``evals.report``'s own section renderers produce, so a future change to either renderer's exact
  wording needs a matching change here — a test pins this by patching a real, current
  ``render_markdown`` output, not a hand-typed fixture string.

Runtime Contract
-----------------
``RaterCaseRow(case_id, language, category, user_turns, system_replies, facts_and_sources, role,
grounding, language_quality, clarification, comment)``.
``load_rater_sheet(path) -> tuple[RaterCaseRow, ...]``.
``score_with_judge(rows, judge) -> tuple[JudgeVerdict, ...]``, one call per row via the given
``LlmJudge``.
``apply_real_judge_validation(report_markdown, agreement, detail, facts_coverage) -> str``: the
report text with the Judge validation section and the stale limitations bullet replaced for a
``human``-provenance ``agreement``; the section is found by its title, never by its number.
``case_scores_csv(rater1, rater2, judge) -> str``: every case's three scores per dimension.
``regenerate_report(rater1_path, rater2_path, report_path, judge, cases_path)``: the whole
orchestration with the judge injected, so it is testable without a model; the report file and the
cases CSV are each replaced atomically.
``main(argv) -> int``: ``python -m evals.h4_judge_validation --rater1 PATH --rater2 PATH [--report
PATH] [--cases PATH]``; ``--report`` defaults to the committed ``reports/evaluation.md`` and
``--cases`` to ``judge-validation-cases.csv`` beside it.

Limitations
-----------
Assumes both sheets already carry the same language, category, turns, replies and facts per case
(checked, not trusted); a genuinely different pair of prepared sheets is a data problem this
module reports rather than silently resolves by picking one side.
"""

from __future__ import annotations

# Standard libraries
import argparse
import csv
import io
import logging
import os
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import get_args

# Local modules
from app.config import load_settings
from app.llm.anthropic_client import AnthropicLlmClient
from evals.facts import NO_KNOWN_FACTS
from evals.judge import JudgeVerdict, LlmJudge
from evals.judge_validation import (
    DIMENSIONS,
    DimensionAgreement,
    DimensionDetail,
    RaterScore,
    Role,
    compute_agreement,
    compute_detail,
)
from evals.report import judge_validation_section

logger = logging.getLogger(__name__)

_DEFAULT_REPORT = Path("reports/evaluation.md")

_PENDING_LIMITATIONS_BULLET = re.compile(
    r"^- The judge-validation section is pending the real H4 human sample; see that "
    r"section for detail\.\n?",
    re.MULTILINE,
)

_JUDGE_VALIDATION_START = re.compile(r"^## \d+\. Judge validation\n\n", re.MULTILINE)
_NEXT_SECTION_BOUNDARY = re.compile(r"\n\n## \d+\.")

_CASES_FILE_NAME = "judge-validation-cases.csv"
_CSV_SOURCES = ("rater1", "rater2", "judge")

_TURN_SEPARATOR = " | "

_COLUMNS = (
    "case_id",
    "language",
    "category",
    "user_turns",
    "system_replies",
    "facts_and_sources",
    "role",
    "grounding",
    "language_quality",
    "clarification",
    "comment",
)

# The columns both sheets must carry identically: everything the raters were shown.
_SHARED_COLUMNS = ("language", "category", "user_turns", "system_replies", "facts_and_sources")

_ROLES: tuple[str, ...] = get_args(Role)

# The rubric's closed 0-2 scale (evals.judge's own _MIN_SCORE/_MAX_SCORE, restated here since a
# CSV column is parsed from plain text, not validated by a pydantic field like the judge's own
# tool-call arguments are).
_MIN_SCORE = 0
_MAX_SCORE = 2


@dataclass(frozen=True, slots=True)
class RaterCaseRow:
    """One row of a returned H4 case sheet, every column the rubric names."""

    case_id: str
    language: str
    category: str
    user_turns: tuple[str, ...]
    system_replies: tuple[str, ...]
    facts_and_sources: str
    role: Role
    grounding: int
    language_quality: int
    clarification: int | None
    comment: str


def _split_turns(text: str) -> tuple[str, ...]:
    """``evals.golden.case_sheet``'s own ``" | "`` join, undone."""
    return tuple(part.strip() for part in text.split(_TURN_SEPARATOR)) if text.strip() else ()


def _parse_score(value: str, *, column: str, case_id: str) -> int:
    try:
        score = int(value)
    except ValueError as exc:
        raise ValueError(f"{case_id}: {column} must be 0, 1 or 2, got {value!r}") from exc
    if not _MIN_SCORE <= score <= _MAX_SCORE:
        raise ValueError(f"{case_id}: {column} must be 0, 1 or 2, got {value!r}")
    return score


def _parse_clarification(value: str, *, case_id: str) -> int | None:
    stripped = value.strip()
    if not stripped or stripped.upper() == "NA":
        return None
    return _parse_score(stripped, column="clarification", case_id=case_id)


def load_rater_sheet(path: Path) -> tuple[RaterCaseRow, ...]:
    """Every row of a returned H4 case sheet.

    Raises
    ------
    ValueError
        The sheet lacks a column, has no rows, repeats a case id, has a short or over-long row,
        carries a role other than ``Rater 1``/``Rater 2``, or has a ``grounding``/
        ``language_quality`` that is not 0, 1 or 2, or a ``clarification`` that is anything but
        0, 1, 2 or ``NA``/empty.
    """
    rows: list[RaterCaseRow] = []
    seen: set[str] = set()
    # utf-8-sig: a spreadsheet export often prefixes the file with a byte-order mark.
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in _COLUMNS if column not in (reader.fieldnames or ())]
        if missing:
            raise ValueError(f"{path.name}: missing column(s) {missing}")
        for line, record in enumerate(reader, start=2):
            if None in record or any(record[column] is None for column in _COLUMNS):
                raise ValueError(f"{path.name}: row at line {line} does not have 11 columns")
            case_id = record["case_id"]
            if case_id in seen:
                raise ValueError(f"{path.name}: case id {case_id!r} appears more than once")
            seen.add(case_id)
            role = record["role"]
            if role not in _ROLES:
                raise ValueError(f"{case_id}: role must be one of {_ROLES}, got {role!r}")
            rows.append(
                RaterCaseRow(
                    case_id=case_id,
                    language=record["language"],
                    category=record["category"],
                    user_turns=_split_turns(record["user_turns"]),
                    system_replies=_split_turns(record["system_replies"]),
                    facts_and_sources=record["facts_and_sources"],
                    role=role,  # type: ignore[arg-type]  # narrowed to Role by the check above
                    grounding=_parse_score(
                        record["grounding"], column="grounding", case_id=case_id
                    ),
                    language_quality=_parse_score(
                        record["language_quality"], column="language_quality", case_id=case_id
                    ),
                    clarification=_parse_clarification(record["clarification"], case_id=case_id),
                    comment=record["comment"],
                )
            )
    if not rows:
        raise ValueError(f"{path.name}: the sheet has no rows")
    return tuple(rows)


def _as_rater_scores(rows: Sequence[RaterCaseRow]) -> tuple[RaterScore, ...]:
    return tuple(
        RaterScore(
            case_id=row.case_id,
            role=row.role,
            grounding=row.grounding,
            language_quality=row.language_quality,
            clarification=row.clarification,
        )
        for row in rows
    )


def _check_roles(rater1: Sequence[RaterCaseRow], rater2: Sequence[RaterCaseRow]) -> None:
    """The first sheet must be Rater 1's and the second Rater 2's, throughout.

    Raises
    ------
    ValueError
        A row's role is not the one its sheet's position implies — notably the same file passed
        twice, which would otherwise report perfect rater-to-rater agreement.
    """
    for expected, rows in (("Rater 1", rater1), ("Rater 2", rater2)):
        wrong = sorted({row.role for row in rows if row.role != expected})
        if wrong:
            raise ValueError(f"the {expected} sheet carries rows with role(s) {wrong}")


def _check_same_prepared_packet(
    rater1: Sequence[RaterCaseRow], rater2: Sequence[RaterCaseRow]
) -> None:
    """Both sheets must carry identical language, category, turns, replies and facts per case —
    they were prepared once, together, before either rater saw a copy.

    Raises
    ------
    ValueError
        The two sheets do not cover the same case ids, or disagree on a shared column for some
        case.
    """
    by_id_1 = {row.case_id: row for row in rater1}
    by_id_2 = {row.case_id: row for row in rater2}
    if by_id_1.keys() != by_id_2.keys():
        raise ValueError(
            "the two rater sheets cover different case ids: "
            f"only in sheet 1: {sorted(by_id_1.keys() - by_id_2.keys())}, "
            f"only in sheet 2: {sorted(by_id_2.keys() - by_id_1.keys())}"
        )
    mismatched = [
        case_id
        for case_id, row1 in by_id_1.items()
        if any(
            getattr(row1, column) != getattr(by_id_2[case_id], column) for column in _SHARED_COLUMNS
        )
    ]
    if mismatched:
        raise ValueError(
            f"the two rater sheets disagree on {'/'.join(_SHARED_COLUMNS)} for case(s) "
            f"{sorted(mismatched)} — they were not prepared from the same packet"
        )


def score_with_judge(rows: Sequence[RaterCaseRow], judge: LlmJudge) -> tuple[JudgeVerdict, ...]:
    """The real judge's own verdict for every case, read from the sheet's own captured
    ``system_replies``/``facts_and_sources`` — never a fresh run of any system."""
    return tuple(
        judge.score(
            row.case_id,
            language=row.language,
            user_turns=row.user_turns,
            system_replies=row.system_replies,
            facts_and_sources=row.facts_and_sources,
        )
        for row in rows
    )


def _judge_validation_bounds(report_markdown: str) -> tuple[int, int]:
    """Start and end offsets of the Judge validation section's body, or ``ValueError`` if the
    shape is unexpected."""
    start = _JUDGE_VALIDATION_START.search(report_markdown)
    if start is None:
        raise ValueError(_UNRECOGNIZED_REPORT)
    end = _NEXT_SECTION_BOUNDARY.search(report_markdown, start.end())
    if end is None:
        raise ValueError(_UNRECOGNIZED_REPORT)
    return start.end(), end.start()


_UNRECOGNIZED_REPORT = (
    "report_markdown does not carry a numbered 'Judge validation' section immediately followed "
    "by another numbered section — refusing to patch a report this module cannot recognize"
)


def apply_real_judge_validation(
    report_markdown: str,
    agreement: tuple[DimensionAgreement, ...],
    detail: tuple[DimensionDetail, ...] | None = None,
    facts_coverage: tuple[int, int] | None = None,
) -> str:
    """``report_markdown`` with its judge-validation section, and the one limitations bullet that
    names it as pending, replaced for a real, ``human``-provenance sample.

    Raises
    ------
    ValueError
        ``report_markdown`` does not carry a numbered Judge validation section followed by another
        numbered section — the report this module was given does not match the shape
        ``evals.report.render_markdown`` produces, so patching it would corrupt rather than update.
    """
    body_start, end = _judge_validation_bounds(report_markdown)
    new_section = judge_validation_section(agreement, "human", detail, facts_coverage)
    patched = report_markdown[:body_start] + new_section + report_markdown[end:]
    return _PENDING_LIMITATIONS_BULLET.sub("", patched)


def _write_atomically(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` so an interruption never leaves a truncated report."""
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _facts_coverage(rows: Sequence[RaterCaseRow]) -> tuple[int, int]:
    """``(rows whose facts column states that none are on record, rows)``."""
    return sum(1 for row in rows if row.facts_and_sources == NO_KNOWN_FACTS), len(rows)


def _score_cell(value: int | None) -> str:
    return "" if value is None else str(value)


def case_scores_csv(
    rater1: Sequence[RaterCaseRow],
    rater2: Sequence[RaterCaseRow],
    judge: Sequence[JudgeVerdict],
) -> str:
    """Every case's three scores per dimension, in the first sheet's row order.

    A blank clarification cell is a case the rubric does not ask that question about. The file
    carries scores only: no conversation text, no rater comments and no judge rationale.
    """
    by_id_2 = {row.case_id: row for row in rater2}
    by_id_judge = {verdict.case_id: verdict for verdict in judge}
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["case_id", "language", "category"]
        + [f"{dimension}_{source}" for dimension in DIMENSIONS for source in _CSV_SOURCES]
    )
    for first in rater1:
        second = by_id_2[first.case_id]
        verdict = by_id_judge[first.case_id]
        cells = [first.case_id, first.language, first.category]
        for dimension in DIMENSIONS:
            for scored in (first, second, verdict):
                cells.append(_score_cell(getattr(scored, dimension)))
        writer.writerow(cells)
    return buffer.getvalue()


def regenerate_report(
    rater1_path: Path,
    rater2_path: Path,
    report_path: Path,
    judge: LlmJudge,
    cases_path: Path | None = None,
) -> tuple[DimensionAgreement, ...]:
    """Score the returned sheets' cases with ``judge`` and patch ``report_path`` in place.

    The per-case scores are written to ``cases_path``, beside the report as
    ``judge-validation-cases.csv`` unless given.

    Every check that can refuse (sheet integrity, roles, packet consistency, report shape) runs
    before the first judge call, so a refusal never follows paid work.

    Raises
    ------
    ValueError
        Any refusal named above; the report file is left untouched.
    """
    rater1_rows = load_rater_sheet(rater1_path)
    rater2_rows = load_rater_sheet(rater2_path)
    _check_roles(rater1_rows, rater2_rows)
    _check_same_prepared_packet(rater1_rows, rater2_rows)
    current = report_path.read_text(encoding="utf-8")
    _judge_validation_bounds(current)

    judge_verdicts = score_with_judge(rater1_rows, judge)
    scores_1, scores_2 = _as_rater_scores(rater1_rows), _as_rater_scores(rater2_rows)
    agreement = compute_agreement(scores_1, scores_2, judge_verdicts)
    detail = compute_detail(scores_1, scores_2, judge_verdicts)
    for entry in agreement:
        logger.info(
            "judge_validation dimension=%s rater_to_rater=%s rater1_to_judge=%s "
            "rater2_to_judge=%s demoted=%s",
            entry.dimension,
            entry.rater_to_rater,
            entry.rater1_to_judge,
            entry.rater2_to_judge,
            entry.demoted,
        )
    target = cases_path or report_path.with_name(_CASES_FILE_NAME)
    _write_atomically(target, case_scores_csv(rater1_rows, rater2_rows, judge_verdicts))
    patched = apply_real_judge_validation(current, agreement, detail, _facts_coverage(rater1_rows))
    _write_atomically(report_path, patched)
    logger.info("judge_validation_report_updated path=%s cases=%s", report_path, target)
    return agreement


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m evals.h4_judge_validation --rater1 PATH --rater2 PATH [--report PATH]``."""
    parser = argparse.ArgumentParser(
        description="Score the returned judge-validation sheets with the real judge and patch "
        "the evaluation report's judge-validation section."
    )
    parser.add_argument("--rater1", type=Path, required=True)
    parser.add_argument("--rater2", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=_DEFAULT_REPORT)
    parser.add_argument("--cases", type=Path, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = load_settings()
    judge = LlmJudge(
        AnthropicLlmClient(settings.require_anthropic_key()), model=settings.judge_model
    )
    regenerate_report(args.rater1, args.rater2, args.report, judge, args.cases)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
