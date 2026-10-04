"""
Judge Validation
================

Overview
--------
Computes the per-dimension agreement the judge rubric names: between the two human raters, and
between each rater and the automated judge, over the same stratified sample of cases. A dimension
whose agreement with the judge falls below the committed 80% threshold is demoted to human-only
scoring for the final report — this module decides that, the report generator only renders it.

Scope
-----
In: pairing rater scores and judge verdicts by ``case_id``, the plain agreement rate per dimension,
and the demotion decision.
Out: producing rater scores (a person, or, until they return theirs, the synthetic placeholder
fixture in ``evals/golden/judge_validation_sample.py``) or judge verdicts (``evals.judge``);
rendering the report's judge-validation section (the report generator).

Design Principles
-----------------
- **Pure functions over plain records.** No I/O, no clock — the same style ``evals.metrics``
  already applies to the deterministic headline metrics, so this module's tests are exact and fast
  regardless of whether the scores being compared are synthetic or the real returned sheets.
- **Clarification is compared only where both sides scored it.** The judge rubric's own ``NA``
  convention means a case with no clarifying question contributes nothing to that dimension's
  agreement, in either direction — never a forced "no disagreement" nor a forced "no data," just
  excluded from that dimension's own denominator.
- **"Not defined" is a value, not an exception.** A dimension with zero comparable pairs (every
  case's clarification score was ``NA`` on at least one side) reports its agreement as the literal
  ``"not defined"``, the same reporting rule ``evals.metrics`` already applies to cost-per-success.
- **Provenance travels with the sample, not with this module.** This module does not know or care
  whether the rater scores it was given are real or the synthetic placeholder; the report
  generator is what refuses to present a synthetic sample's numbers as the real ≥50-case human
  validation (see ``evals.golden.judge_validation_sample``).

Runtime Contract
-----------------
``RaterScore(case_id, role, grounding, language_quality, clarification)``.
``DimensionAgreement(dimension, rater_to_rater, rater1_to_judge, rater2_to_judge, demoted)``.
``compute_agreement(rater1, rater2, judge) -> tuple[DimensionAgreement, ...]``, one entry per
dimension in ``DIMENSIONS`` order.
``PairDetail(compared, weighted_kappa, first_higher, second_higher)`` and
``DimensionDetail(dimension, rater_to_rater, rater1_to_judge, rater2_to_judge)``;
``compute_detail(rater1, rater2, judge) -> tuple[DimensionDetail, ...]``: how many pairs each
agreement rests on, the quadratic-weighted kappa, and which side scored higher where they differ.

Limitations
-----------
The demotion decision uses the plain share of exact score matches, matching the wording of the
demotion rule; the weighted kappa is reported beside it and never changes the decision. The kappa is
"not defined" when both sides of a pair give one and the same score throughout, and it is close to
zero whenever one side's scores barely vary, however often the two sides match — it is read together
with the pair count and the direction counts, not alone. The written analysis of why individual
cases differ (a person naming a cause per disagreement) is not reproduced here. A case_id present in
one input but missing from another is silently excluded from every dimension's comparable set, on
the assumption the three inputs are already the same stratified sample; a test proves a genuinely
mismatched sample does not silently pass as fully compared.
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

_SCALE_MAX = 2


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


@dataclass(frozen=True, slots=True)
class PairDetail:
    """What one agreement rate rests on: its pair count, kappa and the direction of its gaps."""

    compared: int
    weighted_kappa: AgreementValue
    first_higher: int
    """Compared pairs where the first side of the pair scored higher than the second."""
    second_higher: int


@dataclass(frozen=True, slots=True)
class DimensionDetail:
    """One dimension's detail for each of the three pairings."""

    dimension: Dimension
    rater_to_rater: PairDetail
    rater1_to_judge: PairDetail
    rater2_to_judge: PairDetail


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


def _weighted_kappa(pairs: Sequence[tuple[int, int]]) -> AgreementValue:
    """Quadratic-weighted Cohen's kappa over the 0-2 scale, or "not defined" with no spread."""
    size = _SCALE_MAX + 1
    total = len(pairs)
    observed = [[0.0] * size for _ in range(size)]
    for first, second in pairs:
        observed[first][second] += 1
    first_totals = [sum(row) for row in observed]
    second_totals = [sum(observed[row][column] for row in range(size)) for column in range(size)]
    disagreement = 0.0
    chance = 0.0
    for row in range(size):
        for column in range(size):
            weight = (row - column) ** 2 / _SCALE_MAX**2
            disagreement += weight * observed[row][column]
            chance += weight * first_totals[row] * second_totals[column] / total
    if chance == 0:
        return "not defined"
    return 1 - disagreement / chance


def _pair_detail(
    left: Mapping[str, RaterScore | JudgeVerdict],
    right: Mapping[str, RaterScore | JudgeVerdict],
    dimension: Dimension,
) -> PairDetail:
    pairs: list[tuple[int, int]] = []
    for case_id in sorted(left.keys() & right.keys()):
        left_value = _value(left[case_id], dimension)
        right_value = _value(right[case_id], dimension)
        if left_value is not None and right_value is not None:
            pairs.append((left_value, right_value))
    return PairDetail(
        compared=len(pairs),
        weighted_kappa=_weighted_kappa(pairs) if pairs else "not defined",
        first_higher=sum(1 for first, second in pairs if first > second),
        second_higher=sum(1 for first, second in pairs if second > first),
    )


def compute_detail(
    rater1: Sequence[RaterScore],
    rater2: Sequence[RaterScore],
    judge: Sequence[JudgeVerdict],
) -> tuple[DimensionDetail, ...]:
    """Pair counts, weighted kappa and gap direction for the three pairings of each dimension.

    The inputs and the pairing rules are those of ``compute_agreement``; a pair with no score on
    either side is left out, so each ``compared`` is the denominator of the matching agreement.
    """
    by_id_1 = {score.case_id: score for score in rater1}
    by_id_2 = {score.case_id: score for score in rater2}
    by_id_judge = {verdict.case_id: verdict for verdict in judge}
    return tuple(
        DimensionDetail(
            dimension=dimension,
            rater_to_rater=_pair_detail(by_id_1, by_id_2, dimension),
            rater1_to_judge=_pair_detail(by_id_1, by_id_judge, dimension),
            rater2_to_judge=_pair_detail(by_id_2, by_id_judge, dimension),
        )
        for dimension in DIMENSIONS
    )
