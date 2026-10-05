"""
Evaluation Report Generator
=============================

Overview
--------
Turns one full harness run's already-computed results — P's three repeated runs, B0's and B1's
single runs, the live judge's own verdicts over P's last run, and the human judge-validation
sample's agreement — into ``reports/evaluation.md``, the single generated evaluation artifact. Every
piece it reads is produced elsewhere (the runner, the metrics engine, the judge, the
judge-validation agreement computation); this module only assembles and renders what they produced.
The judge-scored-quality section (a system's own live-judge verdicts) and the judge-validation
section (the judge's agreement with human raters) answer two different questions from two different
data sources and are never conflated.

Scope
-----
In: ``EvaluationReport``, the typed input every section renders from, and ``render_markdown``, a
pure function of it.
Out: running any system variant, computing any metric, or calling the judge — this module reads
finished results, exactly the boundary ``pipelines.profile_report`` already draws against
``pipelines.profile`` for the data profile.

Design Principles
-----------------
- **Pure function of the report, like ``pipelines.profile_report``.** No clock, no filesystem, no
  randomness; the same input always renders byte-identical text. The CLI is the only place that
  touches the filesystem, the same split ``pipelines.profile`` already draws with its own renderer.
- **A synthetic judge-validation sample never reaches the report as real.** ``EvaluationReport``
  carries the agreement sample's own ``provenance``; the judge-validation section renders the real
  agreement numbers only when it reads ``"human"``, and a placeholder stating the human sample is
  pending — never a fabricated agreement rate — for anything else, including the synthetic
  placeholder fixture (``evals.golden.judge_validation_sample``). A test proves the two paths render
  different, not just non-empty, text.
- **Every metric states its basis.** ``evals.metrics.Metric.basis`` already carries "measured" or
  "projected"; this renderer surfaces it on every row rather than repeating the OFFLINE
  caveat once and letting a reader forget it applies to every number in the table.
- **A gap this codebase already discloses elsewhere is repeated, not silently duplicated or
  invented.** The learned-component metrics (PR-AUC, calibration, NLU accuracy) are explicitly out
  of ``evals.metrics``'s own scope ("those score a model, not a conversation, and live beside the
  model that produces them"); this report's own Learned-component section says exactly that and
  points at the model experiment log, rather than fabricating a number this module has no way to
  compute.

Runtime Contract
-----------------
``Versions``, ``SystemResult``, ``EvaluationReport``.
``render_markdown(report) -> str``.
``judge_validation_section(agreement, provenance, detail=None, facts_coverage=None) -> str``: the
exact text ``render_markdown`` puts under its Judge validation heading — exported so a cheaper
regeneration of just that section (once the real human sample is available) renders identically to a
full report, never a hand-maintained second copy of the same wording
(``evals.h4_judge_validation``).
``withhold_demoted_judge_means(section_body, agreement, human_means) -> str``: the body of the
Judge-scored quality section with each demoted dimension's judge mean replaced by "not reportable
by the judge" in every system row, and a note beneath the table giving the raters' means; raises
``ValueError`` for a demoted dimension with no column or no rater mean.

Limitations
-----------
The failure gallery reports which deterministic check failed (``correct_outcome``,
``is_unsafe``, an escalation mismatch) or, for a case ``evals.scoring.error_result`` recorded, its
own error message — not a deeper root-cause classification beyond that. ``CaseResult`` itself
carries only those flags, and building a richer taxonomy is outside this module's scope. The failure
gallery draws from ``case_results`` alone, the last run only; the Unsafe outcomes section is the one
that reports every unsafe result from every repeated run, each tagged with which of the harness's
own checks fired (``unsafe_reasons``) and its run's number. That section also states that zero
observed unsafe outcomes does not establish zero risk and sizes the set per golden-set category; a
category's case-runs are its last-run case count times the run count. Repeated-run variability and
the flip list are rendered only for a ``SystemResult`` whose ``run_count`` is greater than one (P
runs three times); B0 and B1 report a single run and show no range, by construction, not because
their own results are omitted.
"""

from __future__ import annotations

# Standard libraries
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

# Local modules
from evals.fairness import (
    CASE_MIX_MIN_FAILURES,
    SMALL_SAMPLE_THRESHOLD,
    CaseProfile,
    Disparity,
    FairnessAnalysis,
    slice_results,
)
from evals.judge import JudgeVerdict
from evals.judge_validation import (
    DEMOTION_THRESHOLD,
    DimensionAgreement,
    DimensionDetail,
    HumanMean,
    PairDetail,
)
from evals.metrics import NOT_DEFINED, CaseResult, Metric
from evals.models import Case, CaseCategory
from evals.repeated_runs import (
    CaseFlip,
    HeadlineMetricsVariability,
    UnsafeOccurrence,
    VariabilityValue,
)

# One (label, accessor) pair per headline metric, in the order the metric definitions list them;
# shared by the headline table and the repeated-run variability table so the two never drift out of
# sync with each other.
_HEADLINE_METRICS: tuple[tuple[str, str], ...] = (
    ("Safe automated resolution", "safe_automated_resolution"),
    ("Attempted share", "attempted_share"),
    ("Conditional automated resolution", "conditional_automated_resolution"),
    ("Containment", "containment"),
    ("Escalation quality", "escalation_quality"),
    ("Missed transfers", "missed_transfers"),
    ("Unnecessary transfers", "unnecessary_transfers"),
    ("Unsafe outcomes", "unsafe_outcomes"),
    ("Latency p50 (s)", "latency_p50"),
    ("Latency p95 (s)", "latency_p95"),
    ("Cost per attempted case (USD)", "cost_per_attempted_case"),
    ("Cost per successful automated resolution (USD)", "cost_per_successful_automated_resolution"),
)


@dataclass(frozen=True, slots=True)
class Versions:
    """Every version the report states, gathered once per report."""

    nlu_model: str
    render_model: str
    judge_model: str
    nlu_prompt_version: str
    render_prompt_version: str
    judge_prompt_version: str
    policy_version: str
    git_sha: str


@dataclass(frozen=True, slots=True)
class SystemResult:
    """One system variant's finished run(s): P has three, B0 and B1 have one each."""

    system: Literal["P", "B0", "B1"]
    run_count: int
    variability: HeadlineMetricsVariability
    """A single run's own degenerate variability (``low == high == mean``) when
    ``run_count == 1``."""
    case_results: tuple[CaseResult, ...]
    """The last run's per-case verdicts, for the failure gallery."""
    flips: tuple[CaseFlip, ...]
    """Empty when ``run_count == 1``: there is nothing to flip across a single run."""
    judge_verdicts: tuple[JudgeVerdict, ...]
    """May be empty if the judge was not run for this system variant."""
    unsafe_occurrences: tuple[UnsafeOccurrence, ...] = ()
    """Every unsafe ``CaseResult`` from every run, not only the last — an unsafe verdict a later
    run did not repeat is otherwise unrecoverable from ``case_results`` alone."""

    def __post_init__(self) -> None:
        if self.run_count < 1:
            raise ValueError("run_count must be at least 1")
        if self.run_count == 1 and self.flips:
            raise ValueError("a single run has nothing to flip")


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Every finished result one full evaluation pass produced, ready to render."""

    versions: Versions
    golden_cases: tuple[Case, ...]
    systems: tuple[SystemResult, ...]
    judge_validation: tuple[DimensionAgreement, ...]
    judge_validation_provenance: Literal["team_generated_synthetic", "human"]
    reference_date: str
    reference_date_source: str
    bank_timezone: str
    scope_note: str = ""
    """Set by the caller when ``golden_cases`` is a subset of the full golden set (for example the
    16-case CI-smoke subset) — empty for a full-golden-set run. Rendered as a prominent callout,
    never silently inferred from a case count this module has no independent way to call "full" or
    "partial"."""
    judge_call_count: int = 0
    """Calls the live judge made while producing this report; zero when it was not run."""
    judge_cost_usd: float | None = None
    """What those calls cost. Evaluation tooling, reported on its own line in the judge-scored
    section and never added to any system's cost; ``None`` when a call went to an unpriced model."""
    case_profiles: Mapping[str, CaseProfile] | None = None
    """The country and segment of each case's customer, by case id, for the fairness slices;
    ``None`` when the caller could not look them up, in which case those slices say so."""


def _count(value: int) -> str:
    return f"{value:,}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    header_row = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join([header_row, separator, body]) if rows else "\n".join([header_row, separator])


def _fmt_variability_value(value: VariabilityValue) -> str:
    if value == NOT_DEFINED:
        return NOT_DEFINED
    return f"{value:.3f}"


def _variability_at(variability: HeadlineMetricsVariability, accessor: str) -> VariabilityValue:
    field = getattr(variability, accessor)
    return field.mean  # type: ignore[no-any-return]


def _workload_section(cases: tuple[Case, ...]) -> str:
    rows = []
    for category in CaseCategory:
        in_category = [case for case in cases if case.category is category]
        if not in_category:
            continue
        by_lang = {
            lang: sum(1 for c in in_category if c.lang == lang) for lang in ("es", "pt", "en")
        }
        provenances = sorted({case.provenance for case in in_category})
        rows.append(
            [
                category.value,
                _count(len(in_category)),
                ", ".join(f"{lang}={n}" for lang, n in by_lang.items() if n),
                ", ".join(provenances),
            ]
        )
    table = _table(["Category", "Count", "Language mix", "Provenance"], rows)
    return f"Total golden-set cases: {_count(len(cases))}.\n\n{table}"


def _versions_section(versions: Versions) -> str:
    rows = [
        ["NLU model", versions.nlu_model],
        ["Render model", versions.render_model],
        ["Judge model", versions.judge_model],
        ["NLU prompt version", versions.nlu_prompt_version],
        ["Render prompt version", versions.render_prompt_version],
        ["Judge prompt version", versions.judge_prompt_version],
        ["Policy corpus version", versions.policy_version],
        ["Git SHA", versions.git_sha],
    ]
    return _table(["Field", "Value"], rows)


def _cost_sample_cell(result: SystemResult, attempted: Sequence[CaseResult]) -> str:
    """The last run's count of attempted cases with a measured cost, stated against the cost
    figure's own basis: that figure averages every run and is undefined when any run is."""
    measured = sum(1 for r in attempted if r.cost_usd is not None)
    cell = f"{_count(measured)} of {_count(len(attempted))}"
    cost_cell = result.variability.cost_per_attempted_case.mean
    if result.run_count > 1 and cost_cell == NOT_DEFINED and measured:
        return f"{cell} (cost is not defined in at least one other run)"
    return cell


def _sample_size_rows(results: Sequence[SystemResult]) -> list[list[str]]:
    """The sample behind the table, per system. Runs is the number of runs the figures average;
    every other row counts the last run's cases, the only run whose per-case results are kept."""
    in_scope = [[r for r in result.case_results if not r.is_adversarial] for result in results]
    attempted = [[r for r in cases if r.automation_attempted] for cases in in_scope]
    return [
        ["Runs", *(_count(result.run_count) for result in results), "count"],
        [
            "Cases (adversarial included)",
            *(_count(len(result.case_results)) for result in results),
            "count, last run",
        ],
        [
            "In-scope cases (denominator of safe resolution, attempted share and containment)",
            *(_count(len(cases)) for cases in in_scope),
            "count, last run",
        ],
        [
            "Attempted cases with a measured cost",
            *(
                _cost_sample_cell(result, cases)
                for result, cases in zip(results, attempted, strict=True)
            ),
            "count, last run",
        ],
    ]


def _headline_table(systems: tuple[SystemResult, ...]) -> str:
    by_system = {result.system: result for result in systems}
    present = [s for s in ("P", "B0", "B1") if s in by_system]
    headers = ["Metric", *present, "Basis"]
    rows = _sample_size_rows([by_system[s] for s in present])
    for label, accessor in _HEADLINE_METRICS:
        row = [label]
        for system in present:
            result = by_system[system]
            value = _variability_at(result.variability, accessor)
            if result.run_count > 1:
                low = _fmt_variability_value(getattr(result.variability, accessor).low)
                high = _fmt_variability_value(getattr(result.variability, accessor).high)
                row.append(f"{_fmt_variability_value(value)} (range {low}-{high})")
            else:
                row.append(_fmt_variability_value(value))
        row.append("measured")
        rows.append(row)
    return _table(headers, rows)


def _judge_dimension_mean(values: Sequence[int]) -> str:
    if not values:
        return NOT_DEFINED
    return f"{sum(values) / len(values):.3f}"


def _judge_cost_line(report: EvaluationReport) -> str:
    """The judge's own spend, kept apart from every system's cost: it is evaluation tooling."""
    cost = NOT_DEFINED if report.judge_cost_usd is None else f"{report.judge_cost_usd:.4f}"
    return (
        f"Judge calls: {_count(report.judge_call_count)}; judge cost: {cost} USD. This is "
        "evaluation tooling cost, reported here only and never included in any system's cost "
        "above."
    )


def _judge_scored_quality_section(report: EvaluationReport) -> str:
    """Aggregate scores the live judge gave a system's own last run — not the judge-vs-human
    agreement of the Judge validation section below, a different question entirely."""
    systems = report.systems
    judged = [result for result in systems if result.judge_verdicts]
    not_judged = [result.system for result in systems if not result.judge_verdicts]
    if not judged:
        return "No system in this report was scored by the live judge."
    rows = []
    for result in judged:
        grounding = [v.grounding for v in result.judge_verdicts]
        language_quality = [v.language_quality for v in result.judge_verdicts]
        clarification = [
            v.clarification for v in result.judge_verdicts if v.clarification is not None
        ]
        rows.append(
            [
                result.system,
                _judge_dimension_mean(grounding),
                _judge_dimension_mean(language_quality),
                _judge_dimension_mean(clarification),
                _count(len(result.judge_verdicts)),
            ]
        )
    table = _table(
        [
            "System",
            "Grounding (mean, 0-2)",
            "Language quality (mean, 0-2)",
            "Clarification (mean, 0-2)",
            "Cases judged",
        ],
        rows,
    )
    note = (
        f"\n\n{', '.join(not_judged)} carried no judge verdicts in this report: a system's own "
        "run is judge-scored only when it is in scope for judge-sourced report metrics (today, "
        "the proposed system alone — the same scope the human judge validation uses)."
        if not_judged
        else ""
    )
    return f"{table}{note}\n\n{_judge_cost_line(report)}"


_WITHHELD_JUDGE_MEAN = "not reportable by the judge"

_JUDGE_MEAN_COLUMNS = {
    "Grounding (mean, 0-2)": "grounding",
    "Language quality (mean, 0-2)": "language_quality",
    "Clarification (mean, 0-2)": "clarification",
}


def _human_mean_text(mean: HumanMean) -> str:
    def one(label: str, value: float | None, scored: int) -> str:
        return f"{label} {NOT_DEFINED}" if value is None else f"{label} {value:.2f} (n={scored})"

    return (
        f"{mean.dimension}: {one('Rater 1', mean.rater1_mean, mean.rater1_scored)}, "
        f"{one('Rater 2', mean.rater2_mean, mean.rater2_scored)}"
    )


def withhold_demoted_judge_means(
    section_body: str,
    agreement: tuple[DimensionAgreement, ...],
    human_means: tuple[HumanMean, ...],
) -> str:
    """The judge-scored quality section's body with each demoted dimension's judge mean replaced.

    A dimension the judge-validation decision demoted shows "not reportable by the judge" in
    every system's row, and a line beneath the table gives the two raters' means for it instead,
    from the validation sample (not the cases the judged run covers). A body with no table (no
    system was judged) is returned unchanged.

    Raises
    ------
    ValueError
        A demoted dimension has no column in the table, or no rater mean was given for it.
    """
    demoted = [entry.dimension for entry in agreement if entry.demoted]
    lines = section_body.split("\n")
    header_at = next(
        (i for i, line in enumerate(lines) if line.startswith("| System |")),
        None,
    )
    if header_at is None or not demoted:
        return section_body
    headers = [cell.strip() for cell in lines[header_at].strip("|").split("|")]
    columns = {
        _JUDGE_MEAN_COLUMNS[header]: index
        for index, header in enumerate(headers)
        if header in _JUDGE_MEAN_COLUMNS
    }
    means = {mean.dimension: mean for mean in human_means}
    missing = [d for d in demoted if d not in columns or d not in means]
    if missing:
        raise ValueError(
            f"the judge-scored quality table has no column or human mean for {missing}"
        )
    end = header_at
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    for row in range(header_at + 2, end):
        cells = [cell.strip() for cell in lines[row].strip("|").split("|")]
        for dimension in demoted:
            cells[columns[dimension]] = _WITHHELD_JUDGE_MEAN
        lines[row] = "| " + " | ".join(cells) + " |"
    note = (
        "**Withheld judge means.** The judge-validation decision demoted "
        + ", ".join(demoted)
        + " to human-only, so the judge's mean for it is not stated. The raters' own means over "
        "the judge-validation sample, a different set of cases from the judged run, are "
        + "; ".join(_human_mean_text(means[d]) for d in demoted)
        + "."
    )
    lines[end:end] = ["", note]
    return "\n".join(lines)


def _repeated_run_section(systems: tuple[SystemResult, ...]) -> str:
    repeated = [result for result in systems if result.run_count > 1]
    if not repeated:
        return "No system variant in this report ran more than once."
    parts = []
    for result in repeated:
        header = f"### {result.system} ({result.run_count} runs)"
        if not result.flips:
            parts.append(f"{header}\n\nNo case flipped its correct-outcome or unsafe verdict.")
            continue
        rows = [
            [
                flip.case_id,
                str(flip.correct_outcome_by_run),
                str(flip.is_unsafe_by_run),
            ]
            for flip in result.flips
        ]
        table = _table(["Case", "correct_outcome by run", "is_unsafe by run"], rows)
        parts.append(f"{header}\n\n{_count(len(result.flips))} case(s) flipped:\n\n{table}")
    return "\n\n".join(parts)


def _failure_gallery(systems: tuple[SystemResult, ...]) -> str:
    rows = []
    for result in systems:
        for case_result in result.case_results:
            failed = (
                case_result.error is not None
                or not case_result.correct_outcome
                or case_result.is_unsafe
            )
            if not failed:
                continue
            if case_result.error is not None:
                failure_class = "error"
                detail = case_result.error
            else:
                failure_class = "unsafe" if case_result.is_unsafe else "incorrect outcome"
                detail = (
                    f"expected_escalation={case_result.expected_escalation}, "
                    f"observed_escalation={case_result.observed_escalation}"
                )
            rows.append([result.system, case_result.case_id, failure_class, detail])
    if not rows:
        return "No case failed a deterministic check in this run."
    return _table(["System", "Case", "Failure class", "Expected vs observed"], rows)


_UNSAFE_CAVEAT = (
    "Zero observed unsafe outcomes does not establish zero risk: it means none occurred in the "
    "case-runs counted below, no more. A system's unsafe-outcome rate in a single run divides by "
    "its cases per run; case-runs is that count times its run count. Repeated runs of the same "
    "cases are not independent trials, so they do not add the evidence that new cases would."
)


def _unsafe_sizing_rows(
    systems: tuple[SystemResult, ...], golden_cases: tuple[Case, ...]
) -> list[list[str]]:
    """Per system and golden-set category: the case-runs observed and the unsafe ones among them.

    A system's case-runs for a category are the cases its last run held in that category times its
    run count; every run of a system executes the same case set. A result whose case is not in
    ``golden_cases`` is counted under "unclassified" rather than dropped.
    """
    category_of = {case.case_id: case.category.value for case in golden_cases}
    rows = []
    for result in systems:
        cases_per_category = Counter(
            category_of.get(r.case_id, "unclassified") for r in result.case_results
        )
        unsafe_per_category = Counter(
            category_of.get(o.result.case_id, "unclassified") for o in result.unsafe_occurrences
        )
        ordered = [c.value for c in CaseCategory if c.value in cases_per_category]
        ordered += sorted(set(cases_per_category) - set(ordered))
        for category in ordered:
            rows.append(
                [
                    result.system,
                    category,
                    _count(cases_per_category[category]),
                    _count(result.run_count),
                    _count(cases_per_category[category] * result.run_count),
                    _count(unsafe_per_category[category]),
                ]
            )
        rows.append(
            [
                result.system,
                "all categories",
                _count(len(result.case_results)),
                _count(result.run_count),
                _count(len(result.case_results) * result.run_count),
                _count(len(result.unsafe_occurrences)),
            ]
        )
    return rows


def _unsafe_outcomes_section(report: EvaluationReport) -> str:
    sizing = _table(
        ["System", "Category", "Cases per run", "Runs", "Case-runs observed", "Unsafe observed"],
        _unsafe_sizing_rows(report.systems, report.golden_cases),
    )
    rows = []
    for result in report.systems:
        for occurrence in result.unsafe_occurrences:
            case_result = occurrence.result
            reasons = ", ".join(case_result.unsafe_reasons) or "unspecified"
            run_label = str(occurrence.run_index + 1) if result.run_count > 1 else "1"
            detail = (
                f"expected_escalation={case_result.expected_escalation}, "
                f"observed_escalation={case_result.observed_escalation}"
            )
            rows.append([result.system, run_label, case_result.case_id, reasons, detail])
    if rows:
        occurrences = _table(
            ["System", "Run", "Case", "Unsafe reason(s)", "Expected vs observed"], rows
        )
    else:
        occurrences = "No unsafe outcome was observed in any run."
    return f"{_UNSAFE_CAVEAT}\n\n{sizing}\n\n{occurrences}"


def _fmt_slice_metric(metric: Metric) -> str:
    if metric.value == NOT_DEFINED:
        return f"{NOT_DEFINED} (n={metric.denominator})"
    return f"{metric.value:.3f} (n={metric.denominator})"


def _category_counts(counts: Sequence[tuple[str, int]]) -> str:
    return ", ".join(f"{category} {count}" for category, count in counts)


def _disparity_note(disparity: Disparity) -> str:
    small = (
        f" Small sample (fewer than {SMALL_SAMPLE_THRESHOLD} in-scope cases)."
        if disparity.small_sample
        else ""
    )
    if not disparity.below_comparison:
        return (
            f"- **{disparity.dimension}: {disparity.label}.** Correct-outcome rate "
            f"{disparity.rate:.3f} (n={disparity.in_scope}) against "
            f"{disparity.comparison_rate:.3f} (n={disparity.comparison_in_scope}) for the rest of "
            "the dimension: above the rest, with non-overlapping 95 % intervals. The difference "
            f"is the rest of the dimension's shortfall, not a failure of this slice.{small}"
        )
    head = (
        f"- **{disparity.dimension}: {disparity.label}.** Correct-outcome rate "
        f"{disparity.rate:.3f} (n={disparity.in_scope}) against {disparity.comparison_rate:.3f} "
        f"(n={disparity.comparison_in_scope}) for the rest of the dimension: below the rest, "
        f"with non-overlapping 95 % intervals.{small}"
    )
    failing = (
        f" Wrong outcome: {', '.join(disparity.failing_case_ids)}."
        if disparity.failing_case_ids
        else ""
    )
    errored = (
        f" Could not run or be scored: {', '.join(disparity.errored_case_ids)}."
        if disparity.errored_case_ids
        else ""
    )
    by_category = (
        f" Failing by category: {_category_counts(disparity.failing_categories)}; the slice's "
        f"in-scope cases by category: {_category_counts(disparity.slice_categories)}."
    )
    concentrated = disparity.concentrated_category()
    if concentrated is not None:
        explanation = (
            f" The failures are concentrated in {concentrated} cases out of proportion to their "
            "share of the slice, so the gap may follow the case mix; re-run the slice with "
            "category-matched cases before attributing it to the slice."
        )
    elif disparity.failure_count < CASE_MIX_MIN_FAILURES:
        explanation = (
            f" Fewer than {CASE_MIX_MIN_FAILURES} failing cases are too few to tell whether they "
            "cluster in a category; the gap is an open investigation."
        )
    else:
        explanation = (
            " The failures follow the slice's own category mix; whether the rest of the dimension "
            "has a different mix is not compared, so the gap is an open investigation, not a "
            "conclusion."
        )
    return f"{head}{failing}{errored}{by_category}{explanation}"


def _unavailable_notice(profiles: Mapping[str, CaseProfile] | None) -> str:
    """The sentence naming a profile dimension no case could be given, or ``""``."""
    if profiles is None:
        absent = ["country", "segment"]
    else:
        absent = [
            name
            for name, found in (
                ("country", any(p.country for p in profiles.values())),
                ("segment", any(p.segment for p in profiles.values())),
            )
            if not found
        ]
        if not absent:
            return ""
    missing = " and ".join(absent).capitalize()
    return (
        f"\n\n{missing} could not be looked up for this run; those cases are in the unknown "
        "slice, so that dimension was not compared."
    )


def _fairness_section(report: EvaluationReport) -> str:
    proposed = next((result for result in report.systems if result.system == "P"), None)
    if proposed is None:
        return "System P was not run, so there is nothing to slice."
    analysis: FairnessAnalysis = slice_results(
        proposed.case_results, report.golden_cases, report.case_profiles or {}
    )
    rows = [
        [
            row.dimension,
            row.label,
            _count(row.cases),
            _count(row.in_scope),
            _fmt_slice_metric(row.correct_outcome),
            _fmt_slice_metric(row.safe_automated_resolution),
            _count(row.unsafe),
            f"small sample (fewer than {SMALL_SAMPLE_THRESHOLD} in-scope cases)"
            if row.small_sample
            else "",
        ]
        for row in analysis.rows
    ]
    table = _table(
        [
            "Dimension",
            "Slice",
            "Cases",
            "In-scope cases",
            "Correct outcome",
            "Safe automated resolution",
            "Unsafe",
            "Sample",
        ],
        rows,
    )
    intro = (
        "System P, last run, sliced by language, country, customer segment and the "
        "accent-flavored phrasing subset (compared with the other Spanish cases). Slices overlap "
        "and are not adjusted for each other or for the category mix. Correct outcome is the "
        "share of in-scope cases with the correct result, whether automated or handed to a "
        "person; safe automated resolution counts only the automated ones, so it also falls "
        "when a slice holds more cases that should go to a person. Only correct outcome drives "
        "the disparity check."
    )
    unavailable = _unavailable_notice(report.case_profiles)
    if analysis.disparities:
        notes = "\n".join(_disparity_note(d) for d in analysis.disparities)
        verdict = (
            "**Slices whose 95 % interval does not overlap the rest of their dimension, each with "
            "an investigation note.** About a dozen comparisons are made, so about one report in "
            "twenty is expected to show at least one flag from chance alone, even when every group "
            "is treated the same.\n\n" + notes
        )
    else:
        verdict = (
            "No slice differs from the rest of its dimension by more than sampling noise "
            "(95 % Wilson intervals that do not overlap). A slice with few cases is rarely "
            "flagged, so the absence of a flag is not evidence of equal treatment."
        )
    return f"{intro}{unavailable}\n\n{table}\n\n{verdict}"


def _agreement_cell(value: VariabilityValue, detail: PairDetail | None) -> str:
    if detail is None:
        return _fmt_variability_value(value)
    kappa = detail.weighted_kappa
    kappa_text = kappa if kappa == NOT_DEFINED else f"{kappa:.2f}"
    return f"{_fmt_variability_value(value)} (n={detail.compared}, kappa {kappa_text})"


def _direction_table(detail: tuple[DimensionDetail, ...]) -> str:
    rows = [
        [
            entry.dimension,
            f"{entry.rater_to_rater.first_higher} / {entry.rater_to_rater.second_higher}",
            f"{entry.rater1_to_judge.second_higher} / {entry.rater1_to_judge.first_higher}",
            f"{entry.rater2_to_judge.second_higher} / {entry.rater2_to_judge.first_higher}",
        ]
        for entry in detail
    ]
    return _table(
        [
            "Dimension",
            "Rater 1 higher / Rater 2 higher",
            "Judge higher / lower than Rater 1",
            "Judge higher / lower than Rater 2",
        ],
        rows,
    )


#: A judge-versus-rater gap is called one-sided from this many differences, when at least this
#: share of them run the same way.
_LEAN_MIN_DIFFERENCES = 5
_LEAN_SHARE = 0.8


def _judge_lean(dimension: str, label: str, pair: PairDetail) -> str | None:
    """A sentence when the judge differs from a rater almost always in the same direction."""
    differences = pair.first_higher + pair.second_higher
    if differences < _LEAN_MIN_DIFFERENCES:
        return None
    if pair.second_higher / differences >= _LEAN_SHARE:
        direction, count = "higher", pair.second_higher
    elif pair.first_higher / differences >= _LEAN_SHARE:
        direction, count = "lower", pair.first_higher
    else:
        return None
    return (
        f"  On {dimension} the judge scores {direction} than {label} in {count} of the "
        f"{differences} cases where they differ, a systematic offset rather than scattered "
        "disagreement."
    )


def _demotion_reason(entry: DimensionAgreement, threshold: str) -> str:
    """Why a dimension was demoted: a rate below the bar, or no pair to compute one from."""
    undefined = [
        label
        for label, value in (
            ("Rater 1", entry.rater1_to_judge),
            ("Rater 2", entry.rater2_to_judge),
        )
        if value == NOT_DEFINED
    ]
    if undefined:
        return (
            f"No case was scored by both {' and '.join(undefined)} and the judge, so the "
            "judge's agreement is not defined, and an agreement that cannot be measured does "
            "not validate the judge."
        )
    return f"The judge's agreement with at least one rater is below {threshold}."


def _validation_decision(
    agreement: tuple[DimensionAgreement, ...],
    detail: tuple[DimensionDetail, ...] | None,
) -> str:
    threshold = f"{DEMOTION_THRESHOLD:.0%}"
    lines = []
    by_dimension = {entry.dimension: entry for entry in detail or ()}
    for entry in agreement:
        if entry.demoted:
            lines.append(
                f"- **{entry.dimension}: not validated.** {_demotion_reason(entry, threshold)} "
                "The judge-scored quality section withholds the judge's mean for it and shows "
                "the raters' mean instead; the raters' per-case scores are in the cases file "
                "the judge-validation command writes, a local working file."
            )
        else:
            lines.append(
                f"- **{entry.dimension}: judge-scored.** The judge agrees with both raters at "
                f"{threshold} or more."
            )
        pairs = by_dimension.get(entry.dimension)
        for label, pair in (
            ("Rater 1", pairs.rater1_to_judge if pairs else None),
            ("Rater 2", pairs.rater2_to_judge if pairs else None),
        ):
            lean = _judge_lean(entry.dimension, label, pair) if pair else None
            if lean:
                lines.append(lean)
        if entry.rater_to_rater != NOT_DEFINED and entry.rater_to_rater < DEMOTION_THRESHOLD:
            lines.append(
                f"  The two raters agree with each other on {entry.dimension} in "
                f"{entry.rater_to_rater:.0%} of cases, below the same bar, so the raters' "
                "scores are themselves not settled and a single rater's score is not a reference."
            )
    reading = (
        "Agreement is the share of cases scored identically. The weighted kappa is the "
        "quadratic-weighted Cohen's kappa over the 0 to 2 scale: it is near zero whenever one "
        "side gives almost the same score to every case, however often the two sides match, so "
        "it is read with the pair count and the direction table, not alone. "
        "Clarification is scored only for the cases the rubric asks the question about, so its "
        "pair count is the number of those cases, shown in its row."
    )
    return "**Decision per dimension**\n\n" + "\n".join(lines) + "\n\n" + reading


def _facts_coverage_note(facts_coverage: tuple[int, int]) -> str:
    without, total = facts_coverage
    return (
        f"**Limitation: the facts column.** {without} of the {total} sheet rows carried the "
        "statement that no case-specific facts are on record: a sheet shows the transaction "
        "of a case the conversation filed, and the policy section a policy question declares, "
        "and nothing else. Raters and judge scored grounding against that statement for those "
        "rows, which is a weaker test of grounding than a reply set beside the facts it should "
        "cite; low agreement on grounding is read with that in mind."
    )


def judge_validation_section(
    agreement: tuple[DimensionAgreement, ...],
    provenance: Literal["team_generated_synthetic", "human"],
    detail: tuple[DimensionDetail, ...] | None = None,
    facts_coverage: tuple[int, int] | None = None,
) -> str:
    """The text of the report's judge-validation section for the given sample provenance.

    ``detail`` adds each agreement's pair count and kappa, the direction table and the decision
    per dimension; ``facts_coverage`` is ``(rows with no case facts, rows)`` and adds the matching
    limitation. Neither is rendered for a sample that is not ``human``.
    """
    if provenance != "human":
        return (
            "**Pending H4.** The judge-validation sample used to produce this section is "
            f"labeled `{provenance}`, not `human` — the real double-scored sample "
            "(see the judge rubric) has not landed yet. No agreement rate "
            "is reported here; presenting a synthetic sample's numbers as the real validation "
            "would misstate how well the judge actually agrees with human raters."
        )
    by_dimension = {entry.dimension: entry for entry in detail or ()}
    rows = []
    for entry in agreement:
        pairs = by_dimension.get(entry.dimension)
        rows.append(
            [
                entry.dimension,
                _agreement_cell(entry.rater_to_rater, pairs.rater_to_rater if pairs else None),
                _agreement_cell(entry.rater1_to_judge, pairs.rater1_to_judge if pairs else None),
                _agreement_cell(entry.rater2_to_judge, pairs.rater2_to_judge if pairs else None),
                "yes (judge mean withheld)" if entry.demoted else "no",
            ]
        )
    table = _table(
        ["Dimension", "Rater-to-rater", "Rater 1-to-judge", "Rater 2-to-judge", "Demoted"], rows
    )
    parts = [f"Judge-validation sample provenance: `human`.\n\n{table}"]
    if detail is not None:
        parts.append(_direction_table(detail))
        parts.append(_validation_decision(agreement, detail))
    if facts_coverage is not None and facts_coverage[0] > 0:
        parts.append(_facts_coverage_note(facts_coverage))
    return "\n\n".join(parts)


_LEARNED_COMPONENT_SECTION = (
    "Risk-model and NLU learned-component metrics (PR-AUC, recall at the validated precision "
    "target, calibration, NLU accuracy/F1 per language) are computed and versioned in the model "
    "experiment log, not here: `evals.metrics`'s own scope explicitly excludes them, since they "
    "score a model, not a conversation."
)


def _limitations_section(report: EvaluationReport) -> str:
    lines = [
        "- All measurements in this report are labeled **measured**; no projected metric (for "
        "example a business-savings projection from cost inputs) is computed here.",
        "- The failure gallery reports which deterministic check failed, not a deeper root-cause "
        "classification.",
        "- A case's cost is the model spend measured for its run: for the proposed system, the "
        "priced understanding calls its turns logged; for B1, every priced call it made. B0 makes "
        "no model call (keyword classifier), so its model cost is zero by construction. Reply "
        "rendering through the model (`MODEL_RENDERER_ENABLED`) logs no cost and is not counted. "
        "A case whose spend could not be measured is left out of the cost denominators "
        "(the sample-size rows of the headline table state how many remain), never counted as "
        "zero. A model call the application could not use (a failed or unusable understanding "
        "call) is not priced and is not counted. The judge's own cost is reported separately in "
        "the judge-scored section.",
        f"- Reference date: {report.reference_date} (source: {report.reference_date_source}, "
        f"bank time zone: {report.bank_timezone}).",
    ]
    if report.judge_validation_provenance != "human":
        lines.append(
            "- The judge-validation section is pending the real H4 human sample; see that "
            "section for detail."
        )
    if report.scope_note:
        lines.append(f"- {report.scope_note}")
    return "\n".join(lines)


def render_markdown(report: EvaluationReport) -> str:
    """Render ``report`` as the Markdown text of ``reports/evaluation.md``.

    Returns
    -------
    str
        The complete report, ending with a newline.
    """
    sections = [
        "# Evaluation Report",
        "All measurements below are **OFFLINE**: run against the held-out golden set, never "
        "against real production traffic. See Limitations for the measured/projected split.",
    ]
    if report.scope_note:
        sections.append(f"**Scope.** {report.scope_note}")
    sections += [
        "## 1. Workload\n\n" + _workload_section(report.golden_cases),
        "## 2. Versions\n\n" + _versions_section(report.versions),
        "## 3. Headline metrics\n\n" + _headline_table(report.systems),
        "## 4. Judge-scored quality\n\n" + _judge_scored_quality_section(report),
        "## 5. Repeated-run variability\n\n" + _repeated_run_section(report.systems),
        "## 6. Failure gallery\n\n" + _failure_gallery(report.systems),
        "## 7. Unsafe outcomes\n\n" + _unsafe_outcomes_section(report),
        "## 8. Fairness and disparity\n\n" + _fairness_section(report),
        "## 9. Judge validation\n\n"
        + judge_validation_section(report.judge_validation, report.judge_validation_provenance),
        "## 10. Learned components\n\n" + _LEARNED_COMPONENT_SECTION,
        "## 11. Limitations\n\n" + _limitations_section(report),
    ]
    return "\n\n".join(sections) + "\n"
