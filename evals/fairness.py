"""
Fairness and Disparity Slicing
===============================

Overview
--------
Slices one system's per-case results by language, country, customer segment and the
accent-flavored phrasing subset, states how many cases stand behind every slice, and flags a slice
whose outcomes differ from the rest of its dimension by more than sampling noise could explain.

Scope
-----
In: ``CaseProfile`` (the country and segment a case's customer has), ``slice_results`` (the slice
rows and the disparity flags) and the small-sample threshold.
Out: looking up a case's customer profile (`evals.profiles`) and rendering (`evals.report`).

Design Principles
-----------------
- **A pure function over plain records.** No I/O: the profile of each case is an input.
- **Every slice states its size.** A rate over three cases is shown with the three, and marked as
  a small sample, never presented as evidence of a gap or of its absence.
- **The disparity test is conservative.** A slice is flagged only when its 95 % Wilson interval
  and its comparison group's do not overlap, so a flag means "larger than noise at this sample
  size"; a small slice is rarely flagged, and the small-sample marker says why it is not read as
  clean.
- **The disparity metric is the correct-outcome rate, not safe automated resolution.** Safe
  automated resolution counts a correct hand-off to a person as a miss by definition, so a slice
  with more human-required cases would look worse for reasons that have nothing to do with the
  slice. Both rates are shown; only the correct-outcome rate drives a flag.
- **A flag carries evidence, not a verdict.** It lists the slice's failing cases and their
  categories next to the slice's own category mix, and says the gap may follow the case mix only
  when the failures are clearly over-represented in one category; otherwise the gap is left
  unexplained rather than attributed.
- **A flag has a direction.** The interval test is symmetric, so a slice that does better than the
  rest is flagged too; it is described as above the rest and given no failure hypothesis.

Runtime Contract
----------------
``slice_results(results, cases, profiles) -> FairnessAnalysis``: pure and deterministic.

Limitations
-----------
Slices overlap (a case is in one language, one country and one segment slice at once) and are not
adjusted for each other or for the category mix; with the golden set's size most slices are small,
which is why the sample size is shown first. A case whose customer profile cannot be found is
counted in an ``unknown`` slice of the dimension, never dropped. The accent-flavored subset is the
set of cases the golden set authors as accent-flavored, compared with the other Spanish cases.
"""

from __future__ import annotations

# Standard libraries
import math  # Wilson interval
from collections import Counter  # Failing-case category mix
from collections.abc import Mapping, Sequence  # Input sequence types
from dataclasses import dataclass  # Immutable result records
from fractions import Fraction  # Exact share comparison

# Local modules
from evals.golden.multilingual import ACCENT_FLAVORED_CASE_IDS  # The accent-flavored subset
from evals.metrics import NOT_DEFINED, CaseResult, Metric, compute_headline_metrics  # Metrics
from evals.models import Case  # The golden case: language and category

#: Slices with fewer in-scope cases than this are marked as small samples.
SMALL_SAMPLE_THRESHOLD = 30

#: The label of a slice holding cases whose profile value could not be found.
UNKNOWN = "unknown"

#: A category is said to hold the slice's failures only with at least this many failing cases, and
#: only when its share of the failures exceeds its share of the slice's in-scope cases by at least
#: ``CASE_MIX_EXCESS``.
CASE_MIX_MIN_FAILURES = 3
CASE_MIX_EXCESS = Fraction(1, 5)

_WILSON_Z = 1.96

LANGUAGE = "language"
COUNTRY = "country"
SEGMENT = "segment"
ACCENT = "accent-flavored phrasing"

#: The accent-flavored subset's own label, and the group it is compared with.
ACCENT_FLAVORED = "accent-flavored"
OTHER_SPANISH = "other Spanish"


@dataclass(frozen=True, slots=True)
class CaseProfile:
    """The country and segment of the customer a case runs as; ``None`` when not found."""

    country: str | None = None
    segment: str | None = None


@dataclass(frozen=True, slots=True)
class SliceRow:
    """One slice of one dimension: its size and its outcomes."""

    dimension: str
    label: str
    cases: int
    """Every result in the slice, adversarial included."""
    in_scope: int
    """The in-scope (non-adversarial) results, the denominator of both rates below."""
    correct_outcome: Metric
    safe_automated_resolution: Metric
    unsafe: int
    """How many results in the slice are unsafe, adversarial included."""
    small_sample: bool
    """Whether ``in_scope`` is below ``SMALL_SAMPLE_THRESHOLD``."""


@dataclass(frozen=True, slots=True)
class Disparity:
    """A slice whose correct-outcome rate differs from its comparison group beyond noise."""

    dimension: str
    label: str
    rate: float
    comparison_rate: float
    in_scope: int
    comparison_in_scope: int
    failing_case_ids: tuple[str, ...]
    """Cases that ran and produced a wrong outcome."""
    errored_case_ids: tuple[str, ...]
    """Cases that could not run or be scored; they count as incorrect."""
    failing_categories: tuple[tuple[str, int], ...]
    """Failing and errored cases by category."""
    slice_categories: tuple[tuple[str, int], ...]
    """The slice's own in-scope cases by category."""

    @property
    def below_comparison(self) -> bool:
        """Whether the slice does worse than the rest of its dimension."""
        return self.rate < self.comparison_rate

    @property
    def small_sample(self) -> bool:
        """Whether the slice has fewer in-scope cases than the small-sample threshold."""
        return self.in_scope < SMALL_SAMPLE_THRESHOLD

    @property
    def failure_count(self) -> int:
        """Failing and errored cases together."""
        return sum(count for _, count in self.failing_categories)

    def concentrated_category(self) -> str | None:
        """The category holding the failures out of proportion to its share of the slice.

        ``None`` when the slice does better than the rest, has too few failures to tell, or its
        failures follow the slice's own category mix.
        """
        if not self.below_comparison or self.failure_count < CASE_MIX_MIN_FAILURES:
            return None
        slice_total = sum(count for _, count in self.slice_categories)
        slice_share = dict(self.slice_categories)
        excess = {
            category: Fraction(count, self.failure_count)
            - Fraction(slice_share.get(category, 0), slice_total)
            for category, count in self.failing_categories
        }
        category = max(excess, key=lambda name: excess[name])
        return category if excess[category] >= CASE_MIX_EXCESS else None


@dataclass(frozen=True, slots=True)
class FairnessAnalysis:
    """Every slice row, and the slices flagged for investigation."""

    rows: tuple[SliceRow, ...]
    disparities: tuple[Disparity, ...]


@dataclass(frozen=True, slots=True)
class _Tagged:
    """One result paired with its golden case (``None`` when unknown) and customer profile."""

    result: CaseResult
    case: Case | None
    profile: CaseProfile


def _wilson_interval(successes: int, total: int) -> tuple[float, float]:
    """The 95 % Wilson score interval of a proportion."""
    proportion = successes / total
    z2 = _WILSON_Z**2
    centre = (proportion + z2 / (2 * total)) / (1 + z2 / total)
    margin = (
        _WILSON_Z
        * math.sqrt(proportion * (1 - proportion) / total + z2 / (4 * total**2))
        / (1 + z2 / total)
    )
    return centre - margin, centre + margin


def _correct_outcome(results: Sequence[CaseResult]) -> Metric:
    """The share of in-scope results with a correct outcome; not defined with none in scope."""
    in_scope = [r for r in results if not r.is_adversarial]
    if not in_scope:
        return Metric(NOT_DEFINED, basis="measured", denominator=0)
    correct = sum(1 for r in in_scope if r.correct_outcome)
    return Metric(correct / len(in_scope), basis="measured", denominator=len(in_scope))


def _row(dimension: str, label: str, group: Sequence[_Tagged]) -> SliceRow:
    """The size and outcome rates of one slice."""
    results = [tagged.result for tagged in group]
    in_scope = sum(1 for r in results if not r.is_adversarial)
    return SliceRow(
        dimension=dimension,
        label=label,
        cases=len(results),
        in_scope=in_scope,
        correct_outcome=_correct_outcome(results),
        safe_automated_resolution=compute_headline_metrics(results).safe_automated_resolution,
        unsafe=sum(1 for r in results if r.is_unsafe),
        small_sample=in_scope < SMALL_SAMPLE_THRESHOLD,
    )


def _disparity(
    dimension: str, label: str, group: Sequence[_Tagged], comparison: Sequence[_Tagged]
) -> Disparity | None:
    """The slice's disparity against ``comparison``, or ``None`` when within noise."""
    in_scope = [t for t in group if not t.result.is_adversarial]
    comparison_in_scope = [t for t in comparison if not t.result.is_adversarial]
    if not in_scope or not comparison_in_scope:
        return None
    correct = sum(1 for t in in_scope if t.result.correct_outcome)
    comparison_correct = sum(1 for t in comparison_in_scope if t.result.correct_outcome)
    low, high = _wilson_interval(correct, len(in_scope))
    comparison_low, comparison_high = _wilson_interval(comparison_correct, len(comparison_in_scope))
    if low <= comparison_high and comparison_low <= high:
        return None
    failing = [t for t in in_scope if not t.result.correct_outcome]
    categories = Counter(t.case.category.value if t.case else UNKNOWN for t in failing)
    slice_categories = Counter(t.case.category.value if t.case else UNKNOWN for t in in_scope)
    return Disparity(
        dimension=dimension,
        label=label,
        rate=correct / len(in_scope),
        comparison_rate=comparison_correct / len(comparison_in_scope),
        in_scope=len(in_scope),
        comparison_in_scope=len(comparison_in_scope),
        failing_case_ids=tuple(sorted(t.result.case_id for t in failing if t.result.error is None)),
        errored_case_ids=tuple(
            sorted(t.result.case_id for t in failing if t.result.error is not None)
        ),
        failing_categories=tuple(sorted(categories.items())),
        slice_categories=tuple(sorted(slice_categories.items())),
    )


def _values(dimension: str, tagged: Sequence[_Tagged]) -> list[str]:
    """Each case's label on ``dimension`` (language, country or segment), in input order."""
    if dimension == LANGUAGE:
        return [t.case.lang if t.case else UNKNOWN for t in tagged]
    if dimension == COUNTRY:
        return [t.profile.country or UNKNOWN for t in tagged]
    return [t.profile.segment or UNKNOWN for t in tagged]


def _ordered(values: Sequence[str]) -> list[str]:
    """The labels sorted, with ``unknown`` last and only when present."""
    labels = sorted({v for v in values if v != UNKNOWN})
    return [*labels, UNKNOWN] if UNKNOWN in values else labels


def slice_results(
    results: Sequence[CaseResult],
    cases: Sequence[Case],
    profiles: Mapping[str, CaseProfile],
) -> FairnessAnalysis:
    """Slice ``results`` by language, country, segment and the accent-flavored subset.

    Parameters
    ----------
    results
        One system's per-case verdicts (a single run), adversarial cases included.
    cases
        The golden cases the results belong to, for each case's language and category.
    profiles
        Each case's customer profile by case id; a case absent from it has an unknown country and
        segment.
    """
    by_id = {case.case_id: case for case in cases}
    tagged = [
        _Tagged(result, by_id.get(result.case_id), profiles.get(result.case_id, CaseProfile()))
        for result in results
    ]
    rows: list[SliceRow] = []
    disparities: list[Disparity] = []
    for dimension in (LANGUAGE, COUNTRY, SEGMENT):
        values = _values(dimension, tagged)
        for label in _ordered(values):
            group = [t for t, v in zip(tagged, values, strict=True) if v == label]
            comparison = [t for t, v in zip(tagged, values, strict=True) if v != label]
            rows.append(_row(dimension, label, group))
            found = _disparity(dimension, label, group, comparison)
            if found is not None:
                disparities.append(found)
    accent = [t for t in tagged if t.result.case_id in ACCENT_FLAVORED_CASE_IDS]
    other_spanish = [
        t
        for t in tagged
        if t.result.case_id not in ACCENT_FLAVORED_CASE_IDS and t.case and t.case.lang == "es"
    ]
    rows.append(_row(ACCENT, ACCENT_FLAVORED, accent))
    rows.append(_row(ACCENT, OTHER_SPANISH, other_spanish))
    found = _disparity(ACCENT, ACCENT_FLAVORED, accent, other_spanish)
    if found is not None:
        disparities.append(found)
    return FairnessAnalysis(rows=tuple(rows), disparities=tuple(disparities))
