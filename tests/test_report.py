"""
Evaluation Report Generator Tests
====================================

Component: ``evals.report``. Hermetic and pure: no clock, no filesystem — the same input always
renders byte-identical text, matching ``pipelines.profile_report``'s own tested guarantee.
"""

from __future__ import annotations

# Standard libraries
from typing import Any

# Third-party libraries
import pytest

# Local modules
from contracts.service_v1.envelope import Intent
from evals.judge_validation import DimensionAgreement
from evals.metrics import (
    CaseResult,
    CostMetrics,
    HeadlineMetrics,
    LatencyMetrics,
    Metric,
    TransferCounts,
)
from evals.models import Case, CaseCategory
from evals.repeated_runs import CaseFlip, UnsafeOccurrence, compute_variability
from evals.report import EvaluationReport, SystemResult, Versions, render_markdown

_MODEL = "claude-sonnet-5"


def _metric(value: float, denominator: int = 10) -> Metric:
    return Metric(value, basis="measured", denominator=denominator)


def _headline(safe: float = 0.5) -> HeadlineMetrics:
    return HeadlineMetrics(
        safe_automated_resolution=_metric(safe),
        attempted_share=_metric(0.9),
        conditional_automated_resolution=_metric(safe),
        containment=_metric(0.8),
        escalation_quality=_metric(0.7),
        transfers=TransferCounts(missed=_metric(0.1), unnecessary=_metric(0.05)),
        unsafe_outcomes=_metric(0.0),
        latency=LatencyMetrics(p50=_metric(1.0), p95=_metric(2.0)),
        cost=CostMetrics(
            per_attempted_case=_metric(0.02), per_successful_automated_resolution=_metric(0.05)
        ),
    )


def _case(**overrides: Any) -> Case:
    values: dict[str, Any] = {
        "case_id": "norm-es-001",
        "category": CaseCategory.NORMAL,
        "lang": "es",
        "provenance": "observed",
        "seed_ref": "ops_seed:CLI-1",
        "user_turns": ("No reconozco un cargo.",),
        "expected_intent": Intent.CONFIRM_FILING,
    }
    return Case(**{**values, **overrides})


def _case_result(**overrides: Any) -> CaseResult:
    defaults: dict[str, Any] = {
        "case_id": "norm-es-001",
        "is_adversarial": False,
        "expected_escalation": False,
        "observed_escalation": False,
        "automation_attempted": True,
        "correct_outcome": True,
    }
    return CaseResult(**{**defaults, **overrides})


def _system(
    system: str = "P",
    run_count: int = 1,
    case_results: tuple[CaseResult, ...] = (),
    unsafe_occurrences: tuple[UnsafeOccurrence, ...] = (),
) -> SystemResult:
    runs = [_headline() for _ in range(run_count)]
    return SystemResult(
        system=system,  # type: ignore[arg-type]
        run_count=run_count,
        variability=compute_variability(runs),
        case_results=case_results,
        flips=(),
        judge_verdicts=(),
        unsafe_occurrences=unsafe_occurrences,
    )


def _versions() -> Versions:
    return Versions(
        nlu_model=_MODEL,
        render_model=_MODEL,
        judge_model=_MODEL,
        nlu_prompt_version="1",
        render_prompt_version="1",
        judge_prompt_version="1",
        policy_version="2",
        git_sha="abc1234",
    )


def _report(**overrides: Any) -> EvaluationReport:
    defaults: dict[str, Any] = {
        "versions": _versions(),
        "golden_cases": (_case(),),
        "systems": (_system(),),
        "judge_validation": (),
        "judge_validation_provenance": "team_generated_synthetic",
        "reference_date": "2026-06-18",
        "reference_date_source": "seeded data",
        "bank_timezone": "America/Bogota",
    }
    return EvaluationReport(**{**defaults, **overrides})


# -----------------------------------------------------------------------------
# SystemResult's own validation
# -----------------------------------------------------------------------------


def test_run_count_must_be_at_least_one() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        SystemResult(
            system="B0",
            run_count=0,
            variability=compute_variability([_headline()]),
            case_results=(),
            flips=(),
            judge_verdicts=(),
        )


def test_a_single_run_cannot_carry_flips() -> None:
    with pytest.raises(ValueError, match="nothing to flip"):
        SystemResult(
            system="B0",
            run_count=1,
            variability=compute_variability([_headline()]),
            case_results=(),
            flips=(CaseFlip("C1", (True, False), (False, False)),),
            judge_verdicts=(),
        )


# -----------------------------------------------------------------------------
# render_markdown — general shape
# -----------------------------------------------------------------------------


def test_render_is_deterministic() -> None:
    report = _report()
    assert render_markdown(report) == render_markdown(report)


def test_render_includes_every_top_level_section() -> None:
    text = render_markdown(_report())
    for heading in (
        "# Evaluation Report",
        "## 1. Workload",
        "## 2. Versions",
        "## 3. Headline metrics",
        "## 4. Repeated-run variability",
        "## 5. Failure gallery",
        "## 6. Unsafe outcomes",
        "## 7. Judge validation",
        "## 8. Learned components",
        "## 9. Limitations",
    ):
        assert heading in text


def test_render_states_offline_and_reference_date() -> None:
    text = render_markdown(_report())
    assert "OFFLINE" in text
    assert "2026-06-18" in text
    assert "America/Bogota" in text


def test_no_scope_note_renders_no_scope_callout() -> None:
    """A full-golden-set run (the default) carries no scope_note and shows no callout."""
    text = render_markdown(_report())
    assert "**Scope.**" not in text


def test_a_scope_note_renders_as_a_prominent_callout_and_in_limitations() -> None:
    text = render_markdown(_report(scope_note="Generated from the 16-case CI-smoke subset."))
    assert "**Scope.** Generated from the 16-case CI-smoke subset." in text
    limitations = text.split("## 9.")[1]
    assert "Generated from the 16-case CI-smoke subset." in limitations


def test_workload_section_counts_the_golden_cases() -> None:
    cases = (
        _case(case_id="c1", category=CaseCategory.NORMAL, lang="es"),
        _case(case_id="c2", category=CaseCategory.NORMAL, lang="pt"),
        _case(case_id="c3", category=CaseCategory.AMBIGUOUS, lang="en"),
    )
    text = render_markdown(_report(golden_cases=cases))

    assert "Total golden-set cases: 3" in text
    assert "normal" in text
    assert "ambiguous" in text


def test_headline_table_lists_only_present_systems() -> None:
    text = render_markdown(_report(systems=(_system("P"), _system("B0"))))

    assert "| Metric | P | B0 | Basis |" in text
    assert "B1" not in text.split("## 3.")[1].split("## 4.")[0]


def test_a_repeated_run_shows_a_range_a_single_run_does_not() -> None:
    p = _system("P", run_count=3)
    b0 = _system("B0", run_count=1)
    text = render_markdown(_report(systems=(p, b0)))

    section = text.split("## 3.")[1].split("## 4.")[0]
    assert "range" in section


# -----------------------------------------------------------------------------
# Failure gallery
# -----------------------------------------------------------------------------


def test_no_failures_states_so_explicitly() -> None:
    text = render_markdown(_report(systems=(_system(case_results=(_case_result(),)),)))
    section = text.split("## 5.")[1].split("## 6.")[0]
    assert "No case failed" in section


def test_a_failed_case_appears_in_the_gallery() -> None:
    failing = _case_result(case_id="norm-es-002", correct_outcome=False)
    text = render_markdown(_report(systems=(_system(case_results=(failing,)),)))
    section = text.split("## 5.")[1].split("## 6.")[0]
    assert "norm-es-002" in section
    assert "incorrect outcome" in section


def test_an_unsafe_case_is_labeled_unsafe_not_incorrect() -> None:
    unsafe = _case_result(case_id="norm-es-003", is_unsafe=True)
    text = render_markdown(_report(systems=(_system(case_results=(unsafe,)),)))
    section = text.split("## 5.")[1].split("## 6.")[0]
    assert "unsafe" in section


def test_an_errored_case_is_labeled_error_not_incorrect_outcome() -> None:
    errored = _case_result(
        case_id="norm-es-004", correct_outcome=False, error="ValueError: bad seed_ref"
    )
    text = render_markdown(_report(systems=(_system(case_results=(errored,)),)))
    section = text.split("## 5.")[1].split("## 6.")[0]
    assert "norm-es-004" in section
    assert "error" in section
    assert "ValueError: bad seed_ref" in section
    assert "incorrect outcome" not in section


# -----------------------------------------------------------------------------
# Unsafe outcomes — every run, not only the last
# -----------------------------------------------------------------------------


def test_no_unsafe_occurrences_states_so_explicitly() -> None:
    text = render_markdown(_report(systems=(_system(),)))
    section = text.split("## 6.")[1].split("## 7.")[0]
    assert "No unsafe outcome was observed" in section


def test_an_unsafe_occurrence_names_its_run_and_reasons() -> None:
    unsafe = _case_result(
        case_id="hr-fraud-en-01",
        expected_escalation=True,
        observed_escalation=False,
        is_unsafe=True,
        unsafe_reasons=("unbacked_handoff",),
    )
    occurrence = UnsafeOccurrence(run_index=0, result=unsafe)
    text = render_markdown(
        _report(systems=(_system(run_count=3, unsafe_occurrences=(occurrence,)),))
    )
    section = text.split("## 6.")[1].split("## 7.")[0]

    assert "hr-fraud-en-01" in section
    assert "unbacked_handoff" in section
    assert "| P | 1 |" in section


def test_every_unsafe_run_is_listed_even_when_only_one_survives_to_case_results() -> None:
    """Run 1's unsafe verdict must still show even though ``case_results`` only keeps run 3."""
    run1_unsafe = _case_result(
        case_id="hr-fraud-en-01", is_unsafe=True, unsafe_reasons=("pii_leaked",)
    )
    run3_safe = _case_result(case_id="hr-fraud-en-01", is_unsafe=False)
    text = render_markdown(
        _report(
            systems=(
                _system(
                    run_count=3,
                    case_results=(run3_safe,),
                    unsafe_occurrences=(UnsafeOccurrence(run_index=0, result=run1_unsafe),),
                ),
            )
        )
    )
    section = text.split("## 6.")[1].split("## 7.")[0]

    assert "hr-fraud-en-01" in section
    assert "pii_leaked" in section


# -----------------------------------------------------------------------------
# Judge validation — the provenance gate (a hard requirement, not a style choice)
# -----------------------------------------------------------------------------


def _agreement() -> tuple[DimensionAgreement, ...]:
    return (
        DimensionAgreement(
            dimension="grounding",
            rater_to_rater=1.0,
            rater1_to_judge=1.0,
            rater2_to_judge=1.0,
            demoted=False,
        ),
    )


def test_a_synthetic_sample_never_renders_an_agreement_rate() -> None:
    """The hard requirement: synthetic placeholder data must never reach the report as if real."""
    text = render_markdown(
        _report(
            judge_validation=_agreement(),
            judge_validation_provenance="team_generated_synthetic",
        )
    )
    section = text.split("## 7.")[1].split("## 8.")[0]

    assert "Pending H4" in section
    assert "1.000" not in section  # the agreement rate itself must not leak through


def test_a_human_sample_renders_the_real_agreement_table() -> None:
    text = render_markdown(
        _report(judge_validation=_agreement(), judge_validation_provenance="human")
    )
    section = text.split("## 7.")[1].split("## 8.")[0]

    assert "Pending H4" not in section
    assert "grounding" in section
    assert "1.000" in section


def test_the_synthetic_and_human_paths_render_different_text() -> None:
    """Revert-check: proves the gate actually branches, not just that it renders *something*."""
    synthetic = render_markdown(
        _report(
            judge_validation=_agreement(),
            judge_validation_provenance="team_generated_synthetic",
        )
    )
    human = render_markdown(
        _report(judge_validation=_agreement(), judge_validation_provenance="human")
    )

    assert synthetic != human


def test_a_demoted_dimension_is_labeled_as_such() -> None:
    demoted = DimensionAgreement(
        dimension="language_quality",
        rater_to_rater=1.0,
        rater1_to_judge=0.5,
        rater2_to_judge=0.5,
        demoted=True,
    )
    text = render_markdown(
        _report(judge_validation=(demoted,), judge_validation_provenance="human")
    )
    section = text.split("## 7.")[1].split("## 8.")[0]

    assert "human-only" in section


# -----------------------------------------------------------------------------
# Learned components and limitations
# -----------------------------------------------------------------------------


def test_learned_components_points_at_the_experiment_log_not_a_fabricated_number() -> None:
    text = render_markdown(_report())
    section = text.split("## 8.")[1].split("## 9.")[0]
    assert "experiment log" in section


def test_limitations_names_pending_h4_only_when_not_human() -> None:
    synthetic = render_markdown(_report(judge_validation_provenance="team_generated_synthetic"))
    human = render_markdown(_report(judge_validation_provenance="human"))

    assert "pending the real H4" in synthetic.split("## 9.")[1]
    assert "pending the real H4" not in human.split("## 9.")[1]
