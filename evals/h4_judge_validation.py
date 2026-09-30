"""
H4 Judge Validation — Real Sample
====================================

Overview
--------
Turns the two returned H4 case sheets (``H4-case-sheet-Rater1.csv``, ``H4-case-sheet-Rater2.csv``)
into ``evals.judge_validation.RaterScore`` tuples, scores the same 50 cases with the real automated
judge, computes the agreement ``evals.judge_validation.compute_agreement`` already implements, and
patches the committed ``reports/evaluation.md`` so its judge-validation section (and the one
limitations bullet that names it as pending) reflect the real, human-provenance sample instead of
the synthetic placeholder.

Scope
-----
In: parsing the returned sheet's own columns (``plan/product/human-tasks/H4-judge-rubric.md``'s
schema), calling the real judge once per case over the sheet's own ``system_replies``/
``facts_and_sources`` (already captured when the sheet was prepared — this module never re-runs a
system to get them), and patching the report.
Out: computing agreement itself (``evals.judge_validation``, unchanged); the judge's own scoring
call (``evals.judge.LlmJudge``, unchanged); the disagreement-analysis writeup
(``H4-disagreement-analysis.md``, a person's own job per the rubric's own "After both files are
returned" section); running the systems that produced ``system_replies``/``facts_and_sources`` in
the first place.

Design Principles
-----------------
- **The two rater sheets are the only source of ``system_replies``/``facts_and_sources``.** The
  rubric's own "Status" note is explicit: those two columns are filled once, when the sheet is
  prepared, from a real run's captured output — not re-derived here, and not assumed identical
  across every row without checking (a mismatch between the two returned sheets on those columns
  means the sheets were not built from the same prepared packet, and this module refuses rather
  than silently trusting one file over the other).
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
``apply_real_judge_validation(report_markdown, agreement) -> str``: the report text with section 7
and the stale limitations bullet replaced for a ``human``-provenance ``agreement``.
``main(argv) -> int``: ``python -m evals.h4_judge_validation --rater1 PATH --rater2 PATH [--report
PATH] [--model MODEL]``; PATH defaults match the real returned-file locations
(``plan/product/human-tasks/returned/H4-case-sheet-Rater{1,2}.csv``) and the committed report
(``reports/evaluation.md``).

Limitations
-----------
Assumes both sheets already carry the same ``system_replies``/``facts_and_sources`` per case
(checked, not trusted); a genuinely different pair of prepared sheets is a data problem this
module reports rather than silently resolves by picking one side.
"""

from __future__ import annotations

# Standard libraries
import argparse
import csv
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# Local modules
from app.config import load_settings
from app.llm.anthropic_client import AnthropicLlmClient
from evals.judge import JudgeVerdict, LlmJudge
from evals.judge_validation import DimensionAgreement, RaterScore, compute_agreement
from evals.report import judge_validation_section

logger = logging.getLogger(__name__)

_DEFAULT_RATER_1 = Path("plan/product/human-tasks/returned/H4-case-sheet-Rater1.csv")
_DEFAULT_RATER_2 = Path("plan/product/human-tasks/returned/H4-case-sheet-Rater2.csv")
_DEFAULT_REPORT = Path("reports/evaluation.md")

_PENDING_LIMITATIONS_BULLET = (
    "- The judge-validation section is pending the real H4 human sample; see that "
    "section for detail.\n"
)

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
    role: str
    grounding: int
    language_quality: int
    clarification: int | None
    comment: str


def _split_turns(text: str) -> tuple[str, ...]:
    """``evals.golden.case_sheet``'s own ``" | "`` join, undone."""
    return tuple(part.strip() for part in text.split("|")) if text.strip() else ()


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
        A row's ``grounding``/``language_quality`` is not filled with 0, 1 or 2, or
        ``clarification`` is filled with something other than 0, 1, 2 or ``NA``/empty.
    """
    rows = []
    with path.open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            case_id = record["case_id"]
            rows.append(
                RaterCaseRow(
                    case_id=case_id,
                    language=record["language"],
                    category=record["category"],
                    user_turns=_split_turns(record["user_turns"]),
                    system_replies=_split_turns(record["system_replies"]),
                    facts_and_sources=record["facts_and_sources"],
                    role=record["role"],
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
    return tuple(rows)


def _as_rater_scores(rows: Sequence[RaterCaseRow]) -> tuple[RaterScore, ...]:
    return tuple(
        RaterScore(
            case_id=row.case_id,
            role=row.role,  # type: ignore[arg-type]
            grounding=row.grounding,
            language_quality=row.language_quality,
            clarification=row.clarification,
        )
        for row in rows
    )


def _check_same_prepared_packet(
    rater1: Sequence[RaterCaseRow], rater2: Sequence[RaterCaseRow]
) -> None:
    """Both sheets must carry identical ``system_replies``/``facts_and_sources`` per case — they
    were prepared once, together, before either rater saw a copy.

    Raises
    ------
    ValueError
        The two sheets disagree on ``system_replies`` or ``facts_and_sources`` for some case, or
        do not cover the same set of case ids.
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
        if (row1.system_replies, row1.facts_and_sources)
        != (by_id_2[case_id].system_replies, by_id_2[case_id].facts_and_sources)
    ]
    if mismatched:
        raise ValueError(
            "the two rater sheets disagree on system_replies/facts_and_sources for case(s) "
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


def apply_real_judge_validation(
    report_markdown: str, agreement: tuple[DimensionAgreement, ...]
) -> str:
    """``report_markdown`` with its judge-validation section, and the one limitations bullet that
    names it as pending, replaced for a real, ``human``-provenance sample.

    Raises
    ------
    ValueError
        ``report_markdown`` does not carry a ``## 7. Judge validation`` section immediately
        followed by ``## 8.`` — the report this module was given does not match the shape
        ``evals.report.render_markdown`` produces, so patching it would corrupt rather than update.
    """
    start_marker = "## 7. Judge validation\n\n"
    end_marker = "\n\n## 8."
    start = report_markdown.find(start_marker)
    end = report_markdown.find(end_marker)
    if start == -1 or end == -1 or end < start:
        raise ValueError(
            "report_markdown does not carry a '## 7. Judge validation' section immediately "
            "followed by '## 8.' — refusing to patch a report this module cannot recognize"
        )
    section_start = start + len(start_marker)
    new_section = judge_validation_section(agreement, "human")
    patched = report_markdown[:section_start] + new_section + report_markdown[end:]
    return patched.replace(_PENDING_LIMITATIONS_BULLET, "")


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m evals.h4_judge_validation --rater1 PATH --rater2 PATH [--report PATH]``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rater1", type=Path, default=_DEFAULT_RATER_1)
    parser.add_argument("--rater2", type=Path, default=_DEFAULT_RATER_2)
    parser.add_argument("--report", type=Path, default=_DEFAULT_REPORT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    rater1_rows = load_rater_sheet(args.rater1)
    rater2_rows = load_rater_sheet(args.rater2)
    _check_same_prepared_packet(rater1_rows, rater2_rows)

    settings = load_settings()
    judge = LlmJudge(
        AnthropicLlmClient(settings.require_anthropic_key()), model=settings.judge_model
    )
    judge_verdicts = score_with_judge(rater1_rows, judge)

    agreement = compute_agreement(
        _as_rater_scores(rater1_rows), _as_rater_scores(rater2_rows), judge_verdicts
    )
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

    current = args.report.read_text(encoding="utf-8")
    args.report.write_text(apply_real_judge_validation(current, agreement), encoding="utf-8")
    logger.info("judge_validation_report_updated path=%s", args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
