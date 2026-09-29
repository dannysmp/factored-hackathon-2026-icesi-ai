"""
Judge Validation (H4)
======================

Overview
--------
Computes the per-dimension agreement ``plan/docs/evaluation-plan.md``'s Judge validation section
and ``plan/product/human-tasks/H4-judge-rubric.md`` both name: between the two human raters, and
between each rater and the automated judge, over the same stratified sample of cases. A dimension
whose agreement with the judge falls below the committed 80% threshold is demoted to human-only
scoring for the final report — this module decides that, the report generator only renders it.

Scope
-----
In: pairing rater scores and judge verdicts by ``case_id``, the plain agreement rate per dimension,
and the demotion decision.
Out: producing rater scores (a person, or, until they return theirs, the synthetic placeholder
fixture in ``evals/golden/judge_validation_sample.py``) or judge verdicts (``evals.judge``);
weighted kappa (see Limitations); rendering the report's judge-validation section (the report
generator).

Design Principles
-----------------
- **Pure functions over plain records.** No I/O, no clock — the same style ``evals.metrics``
  already applies to the deterministic headline metrics, so this module's tests are exact and fast
  regardless of whether the scores being compared are synthetic or the real returned sheets.
- **Clarification is compared only where both sides scored it.** ``H4-judge-rubric.md``'s own
  ``NA`` convention means a case with no clarifying question contributes nothing to that
  dimension's agreement, in either direction — never a forced "no disagreement" nor a forced
  "no data," just excluded from that dimension's own denominator.
- **"Not defined" is a value, not an exception.** A dimension with zero comparable pairs (every
  case's clarification score was ``NA`` on at least one side) reports its agreement as the literal
  ``"not defined"``, the same reporting rule ``evals.metrics`` already applies to cost-per-success.
- **Provenance travels with the sample, not with this module.** This module does not know or care
  whether the rater scores it was given are real or the synthetic placeholder; the report
  generator is what refuses to present a synthetic sample's numbers as the real ≥50-case human
  validation the plan requires (see ``evals.golden.judge_validation_sample``).

Runtime Contract
-----------------
``RaterScore(case_id, role, grounding, language_quality, clarification)``.
``DimensionAgreement(dimension, rater_to_rater, rater1_to_judge, rater2_to_judge, demoted)``.
``compute_agreement(rater1, rater2, judge) -> tuple[DimensionAgreement, ...]``, one entry per
dimension in ``DIMENSIONS`` order.

Limitations
-----------
Agreement is the plain share of exact score matches, not a weighted kappa; the human-only
disagreement analysis (``H4-disagreement-analysis.md``, a person naming a cause per disagreement)
is not reproduced here — this module only decides whether a dimension crosses the demotion
threshold, matching the wording of ``evaluation-plan.md``'s own demotion rule, which cites a plain
agreement percentage. A case_id present in one input but missing from another is silently excluded
from every dimension's comparable set, on the assumption the three inputs are already the same
stratified sample; a test proves a genuinely mismatched sample does not silently pass as fully
compared.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

# Local modules
from evals.judge import JudgeVerdict

DEMOTION_THRESHOLD = 0.8

Dimension = Literal["grounding", "language_quality", "clarification"]
DIMENSIONS: tuple[Dimension, ...] = ("grounding", "language_quality", "clarification")

Role = Literal["Rater 1", "Rater 2"]

AgreementValue = float | Literal["not defined"]


@dataclass(frozen=True, slots=True)
class RaterScore:
    """One human rater's score for one case, in the same shape ``JudgeVerdict`` scores it."""

    case_id: str
    role: Role
    grounding: int
    language_quality: int
    clarification: int | None = None


@dataclass(frozen=True, slots=True)
class DimensionAgreement:
    """One dimension's agreement rates and the demotion decision they produce."""

    dimension: Dimension
    rater_to_rater: AgreementValue
    rater1_to_judge: AgreementValue
    rater2_to_judge: AgreementValue
    demoted: bool
    """True when either rater's agreement with the judge is below ``DEMOTION_THRESHOLD``, or is
    "not defined" (zero comparable cases is not evidence the judge agrees)."""


def _value(score: RaterScore | JudgeVerdict, dimension: Dimension) -> int | None:
    return getattr(score, dimension)  # type: ignore[no-any-return]


def _agreement(
    left: Mapping[str, RaterScore | JudgeVerdict],
    right: Mapping[str, RaterScore | JudgeVerdict],
    dimension: Dimension,
) -> AgreementValue:
    matches = 0
    comparable = 0
    for case_id in left.keys() & right.keys():
        left_value = _value(left[case_id], dimension)
        right_value = _value(right[case_id], dimension)
        if left_value is None or right_value is None:
            continue
        comparable += 1
        if left_value == right_value:
            matches += 1
    if comparable == 0:
        return "not defined"
    return matches / comparable


def _demoted(rater1_to_judge: AgreementValue, rater2_to_judge: AgreementValue) -> bool:
    return any(
        value == "not defined" or value < DEMOTION_THRESHOLD
        for value in (rater1_to_judge, rater2_to_judge)
    )


def compute_agreement(
    rater1: Sequence[RaterScore],
    rater2: Sequence[RaterScore],
    judge: Sequence[JudgeVerdict],
) -> tuple[DimensionAgreement, ...]:
    """Per-dimension agreement between the two raters and each rater and the judge.

    Every sequence is expected to cover the same stratified sample of ``case_id``\\ s (see this
    module's Limitations for what a mismatched sample does); each is indexed here by its own
    ``case_id``, so a duplicate id in any one sequence would silently overwrite an earlier entry —
    a golden-set-style uniqueness rule the caller's own data is expected to already hold.
    """
    by_id_1 = {score.case_id: score for score in rater1}
    by_id_2 = {score.case_id: score for score in rater2}
    by_id_judge = {verdict.case_id: verdict for verdict in judge}
    results = []
    for dimension in DIMENSIONS:
        rater_to_rater = _agreement(by_id_1, by_id_2, dimension)
        rater1_to_judge = _agreement(by_id_1, by_id_judge, dimension)
        rater2_to_judge = _agreement(by_id_2, by_id_judge, dimension)
        results.append(
            DimensionAgreement(
                dimension=dimension,
                rater_to_rater=rater_to_rater,
                rater1_to_judge=rater1_to_judge,
                rater2_to_judge=rater2_to_judge,
                demoted=_demoted(rater1_to_judge, rater2_to_judge),
            )
        )
    return tuple(results)
