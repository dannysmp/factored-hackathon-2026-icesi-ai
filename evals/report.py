"""
Evaluation Report Generator
=============================

Overview
--------
Turns one full harness run's already-computed results — P's three repeated runs, B0's and B1's
single runs, the live judge's own verdicts over P's last run, and the H4 judge-validation sample's
agreement — into ``reports/evaluation.md``, the single generated artifact
``plan/docs/evaluation-plan.md``'s Report section names. The first full ``make evaluate`` this
slice enables is exactly this: every piece this module reads was built by an earlier slice (the
runner, the metrics engine, the judge, the judge-validation agreement computation); this module
only assembles and renders what they already produced. The judge-scored-quality section (a
system's own live-judge verdicts) and the judge-validation section (the judge's agreement with
human raters) answer two different questions from two different data sources and are never
conflated.

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
  randomness; the same input always renders byte-identical text. The CLI (a later piece of this
  same slice) is the only place that touches the filesystem, the same split ``pipelines.profile``
  already draws with its own renderer.
- **A synthetic judge-validation sample never reaches the report as real.** ``EvaluationReport``
  carries the agreement sample's own ``provenance``; the judge-validation section renders the real
  agreement numbers only when it reads ``"human"``, and a "pending H4" placeholder — never a
  fabricated agreement rate — for anything else, including this slice's own synthetic placeholder
  fixture (``evals.golden.judge_validation_sample``). A test proves the two paths render
  different, not just non-empty, text.
- **Every metric states its basis.** ``evals.metrics.Metric.basis`` already carries "measured" or
  "projected"; this renderer surfaces it on every row rather than repeating the plan's own
  OFFLINE caveat once and letting a reader forget it applies to every number in the table.
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

Limitations
-----------
The failure gallery reports which deterministic check failed (``correct_outcome``,
``is_unsafe``, an escalation mismatch) or, for a case ``evals.scoring.error_result`` recorded,
its own error message — not a deeper root-cause classification beyond that. ``CaseResult`` itself
carries only those flags, and building a richer taxonomy is not this slice's own scope. The
failure gallery draws from ``case_results`` alone, the last run only; the Unsafe outcomes section
is the one that reports every unsafe result from every repeated run, each tagged with which of
the harness's own checks fired (``unsafe_reasons``) and its run's number.
Repeated-run variability and the flip list are rendered only for a ``SystemResult`` whose
``run_count`` is greater than one (P, by the plan's own execution protocol); B0 and B1 report a
single run and show no range, by construction, not because their own results are omitted.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

# Local modules
from evals.judge import JudgeVerdict
from evals.judge_validation import DimensionAgreement
from evals.metrics import NOT_DEFINED, CaseResult
from evals.models import Case, CaseCategory
from evals.repeated_runs import (
    CaseFlip,
    HeadlineMetricsVariability,
    UnsafeOccurrence,
    VariabilityValue,
)

# One (label, accessor) pair per headline metric, in the order the plan's own Metric definitions
# section lists them; shared by the headline table and the repeated-run variability table so the
# two never drift out of sync with each other.
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
    """Every version the plan's Report section asks for, gathered once per report."""

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
    16-case CI-smoke subset, run before the full adversarial set's loader lands) — empty for a
    full-golden-set run. Rendered as a prominent callout, never silently inferred from a case
    count this module has no independent way to call "full" or "partial"."""


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


def _headline_table(systems: tuple[SystemResult, ...]) -> str:
    by_system = {result.system: result for result in systems}
    present = [s for s in ("P", "B0", "B1") if s in by_system]
    headers = ["Metric", *present, "Basis"]
    rows = []
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


def _judge_scored_quality_section(systems: tuple[SystemResult, ...]) -> str:
    """Aggregate scores the live judge gave a system's own last run — not the judge-vs-human
    agreement of the Judge validation section below, a different question entirely."""
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
        "the proposed system alone — the same scope H4's own human validation uses)."
        if not_judged
        else ""
    )
    return f"{table}{note}"


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


def _unsafe_outcomes_section(systems: tuple[SystemResult, ...]) -> str:
    rows = []
    for result in systems:
        for occurrence in result.unsafe_occurrences:
            case_result = occurrence.result
            reasons = ", ".join(case_result.unsafe_reasons) or "unspecified"
            run_label = str(occurrence.run_index + 1) if result.run_count > 1 else "1"
            detail = (
                f"expected_escalation={case_result.expected_escalation}, "
                f"observed_escalation={case_result.observed_escalation}"
            )
            rows.append([result.system, run_label, case_result.case_id, reasons, detail])
    if not rows:
        return "No unsafe outcome was observed in any run."
    return _table(["System", "Run", "Case", "Unsafe reason(s)", "Expected vs observed"], rows)


def _judge_validation_section(
    agreement: tuple[DimensionAgreement, ...],
    provenance: Literal["team_generated_synthetic", "human"],
) -> str:
    if provenance != "human":
        return (
            "**Pending H4.** The judge-validation sample used to produce this section is "
            f"labeled `{provenance}`, not `human` — the real double-scored sample "
            "(plan/product/human-tasks/H4-judge-rubric.md) has not landed yet. No agreement rate "
            "is reported here; presenting a synthetic sample's numbers as the real validation "
            "would misstate how well the judge actually agrees with human raters."
        )
    rows = [
        [
            entry.dimension,
            _fmt_variability_value(entry.rater_to_rater),
            _fmt_variability_value(entry.rater1_to_judge),
            _fmt_variability_value(entry.rater2_to_judge),
            "yes (human-only in this report)" if entry.demoted else "no",
        ]
        for entry in agreement
    ]
    table = _table(
        ["Dimension", "Rater-to-rater", "Rater 1-to-judge", "Rater 2-to-judge", "Demoted"], rows
    )
    return f"Judge-validation sample provenance: `human`.\n\n{table}"


_LEARNED_COMPONENT_SECTION = (
    "Risk-model and NLU learned-component metrics (PR-AUC, recall at the validated precision "
    "target, calibration, NLU accuracy/F1 per language) are computed and versioned in the model "
    "experiment log, not here: `evals.metrics`'s own scope explicitly excludes them, since they "
    "score a model, not a conversation."
)


def _limitations_section(report: EvaluationReport) -> str:
    lines = [
        "- All measurements in this report are labeled **measured**; no projected metric (for "
        "example a business-savings projection from cost inputs) is computed by this slice.",
        "- The failure gallery reports which deterministic check failed, not a deeper root-cause "
        "classification.",
        "- Cost per attempted case and cost per successful automated resolution are always "
        '"not defined": `evals.scoring.score_case` never populates a `CaseResult`\'s `cost_usd` '
        "(per-case cost accounting is a later increment's job); a system's real spend is only "
        "computable from its own structured logs, not from this report.",
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
        "## 4. Judge-scored quality\n\n" + _judge_scored_quality_section(report.systems),
        "## 5. Repeated-run variability\n\n" + _repeated_run_section(report.systems),
        "## 6. Failure gallery\n\n" + _failure_gallery(report.systems),
        "## 7. Unsafe outcomes\n\n" + _unsafe_outcomes_section(report.systems),
        "## 8. Judge validation\n\n"
        + _judge_validation_section(report.judge_validation, report.judge_validation_provenance),
        "## 9. Learned components\n\n" + _LEARNED_COMPONENT_SECTION,
        "## 10. Limitations\n\n" + _limitations_section(report),
    ]
    return "\n\n".join(sections) + "\n"
