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
from app.domain.policy.models import DisputeCategory
from contracts.service_v1.envelope import Intent
from evals.fairness import CaseProfile
from evals.judge import JudgeVerdict
from evals.judge_validation import DimensionAgreement, DimensionDetail, PairDetail
from evals.metrics import (
    NOT_DEFINED,
    CaseResult,
    CostMetrics,
    HeadlineMetrics,
    LatencyMetrics,
    Metric,
    TransferCounts,
    compute_headline_metrics,
)
from evals.models import Case, CaseCategory, SafeBehavior
from evals.repeated_runs import CaseFlip, UnsafeOccurrence, compute_variability
from evals.report import (
    EvaluationReport,
    SystemResult,
    Versions,
    judge_validation_section,
    render_markdown,
)

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
        "seed_ref": "ops_seed:TRX-1",
        "user_turns": ("No reconozco un cargo.",),
        "expected_intent": Intent.CONFIRM_FILING,
        "expected_category": DisputeCategory.UNRECOGNIZED_CHARGE,
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


def _judge_verdict(**overrides: Any) -> JudgeVerdict:
    defaults: dict[str, Any] = {
        "case_id": "norm-es-001",
        "grounding": 2,
        "language_quality": 2,
        "clarification": None,
        "rationale": "Grounded and natural.",
        "judge_model": _MODEL,
        "prompt_version": "1",
    }
    return JudgeVerdict(**{**defaults, **overrides})


def _system(
    system: str = "P",
    run_count: int = 1,
    case_results: tuple[CaseResult, ...] = (),
    unsafe_occurrences: tuple[UnsafeOccurrence, ...] = (),
    judge_verdicts: tuple[JudgeVerdict, ...] = (),
) -> SystemResult:
    runs = [_headline() for _ in range(run_count)]
    return SystemResult(
        system=system,  # type: ignore[arg-type]
        run_count=run_count,
        variability=compute_variability(runs),
        case_results=case_results,
        flips=(),
        judge_verdicts=judge_verdicts,
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
        "## 4. Judge-scored quality",
        "## 5. Repeated-run variability",
        "## 6. Failure gallery",
        "## 7. Unsafe outcomes",
        "## 8. Fairness and disparity",
        "## 9. Judge validation",
        "## 10. Learned components",
        "## 11. Limitations",
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
    limitations = text.split("## 11.")[1]
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
# Judge-scored quality — the live judge's own verdicts over a system's last run
# -----------------------------------------------------------------------------


def test_no_system_judged_states_so_explicitly() -> None:
    text = render_markdown(_report(systems=(_system("P"), _system("B0"))))
    section = text.split("## 4.")[1].split("## 5.")[0]
    assert "No system in this report was scored by the live judge." in section


def test_a_judged_system_shows_mean_scores_and_the_count_judged() -> None:
    verdicts = (
        _judge_verdict(case_id="c1", grounding=2, language_quality=2),
        _judge_verdict(case_id="c2", grounding=0, language_quality=2),
    )
    text = render_markdown(_report(systems=(_system("P", judge_verdicts=verdicts),)))
    section = text.split("## 4.")[1].split("## 5.")[0]

    assert "| P | 1.000 | 2.000 |" in section
    assert "| 2 |" in section  # cases judged


def test_clarification_mean_excludes_na_cases() -> None:
    verdicts = (
        _judge_verdict(case_id="c1", clarification=None),
        _judge_verdict(case_id="c2", clarification=2),
        _judge_verdict(case_id="c3", clarification=0),
    )
    text = render_markdown(_report(systems=(_system("P", judge_verdicts=verdicts),)))
    section = text.split("## 4.")[1].split("## 5.")[0]

    # (2 + 0) / 2, not / 3: the NA case must not silently pull the mean down.
    assert "1.000" in section


def test_a_system_with_no_judge_verdicts_is_named_not_silently_omitted() -> None:
    verdicts = (_judge_verdict(),)
    text = render_markdown(_report(systems=(_system("P", judge_verdicts=verdicts), _system("B0"))))
    section = text.split("## 4.")[1].split("## 5.")[0]

    assert "B0" in section
    assert "carried no judge verdicts" in section


# -----------------------------------------------------------------------------
# Failure gallery
# -----------------------------------------------------------------------------


def test_no_failures_states_so_explicitly() -> None:
    text = render_markdown(_report(systems=(_system(case_results=(_case_result(),)),)))
    section = text.split("## 6.")[1].split("## 7.")[0]
    assert "No case failed" in section


def test_a_failed_case_appears_in_the_gallery() -> None:
    failing = _case_result(case_id="norm-es-002", correct_outcome=False)
    text = render_markdown(_report(systems=(_system(case_results=(failing,)),)))
    section = text.split("## 6.")[1].split("## 7.")[0]
    assert "norm-es-002" in section
    assert "incorrect outcome" in section


def test_an_unsafe_case_is_labeled_unsafe_not_incorrect() -> None:
    unsafe = _case_result(case_id="norm-es-003", is_unsafe=True)
    text = render_markdown(_report(systems=(_system(case_results=(unsafe,)),)))
    section = text.split("## 6.")[1].split("## 7.")[0]
    assert "unsafe" in section


def test_an_errored_case_is_labeled_error_not_incorrect_outcome() -> None:
    errored = _case_result(
        case_id="norm-es-004", correct_outcome=False, error="ValueError: bad seed_ref"
    )
    text = render_markdown(_report(systems=(_system(case_results=(errored,)),)))
    section = text.split("## 6.")[1].split("## 7.")[0]
    assert "norm-es-004" in section
    assert "error" in section
    assert "ValueError: bad seed_ref" in section
    assert "incorrect outcome" not in section


# -----------------------------------------------------------------------------
# Unsafe outcomes — every run, not only the last
# -----------------------------------------------------------------------------


def test_no_unsafe_occurrences_states_so_explicitly() -> None:
    text = render_markdown(_report(systems=(_system(),)))
    section = text.split("## 7.")[1].split("## 8.")[0]
    assert "No unsafe outcome was observed" in section


def _categorized(case_id: str, category: CaseCategory) -> Case:
    if category is CaseCategory.ADVERSARIAL:
        return _case(case_id=case_id, category=category, expected_safe_behavior=SafeBehavior.REFUSE)
    return _case(case_id=case_id, category=category)


def test_the_unsafe_section_says_zero_observed_does_not_establish_zero_risk() -> None:
    text = render_markdown(_report(systems=(_system(),)))
    section = text.split("## 7.")[1].split("## 8.")[0]

    assert "does not establish zero risk" in section


def test_the_unsafe_caveat_does_not_call_the_sizes_the_denominator_of_every_rate() -> None:
    section = render_markdown(_report()).split("## 7.")[1].split("## 8.")[0]

    assert "denominators of every" not in section
    assert "not independent trials" in section
    assert "in a set this small" not in section


def test_the_unsafe_section_sizes_the_set_per_category_and_per_system() -> None:
    golden = (
        _categorized("n-1", CaseCategory.NORMAL),
        _categorized("n-2", CaseCategory.NORMAL),
        _categorized("a-1", CaseCategory.ADVERSARIAL),
    )
    results = (
        _case_result(case_id="n-1"),
        _case_result(case_id="n-2"),
        _case_result(case_id="a-1", is_adversarial=True),
    )
    unsafe = UnsafeOccurrence(
        run_index=1, result=_case_result(case_id="a-1", is_adversarial=True, is_unsafe=True)
    )
    p = _system("P", run_count=3, case_results=results, unsafe_occurrences=(unsafe,))
    b0 = _system("B0", run_count=1, case_results=results[:2])
    text = render_markdown(_report(golden_cases=golden, systems=(p, b0)))
    rows = {
        tuple(cell.strip() for cell in line.strip("|").split("|"))[:6]
        for line in text.split("## 7.")[1].split("## 8.")[0].splitlines()
        if line.startswith("|")
    }

    assert ("P", "normal", "2", "3", "6", "0") in rows
    assert ("P", "adversarial", "1", "3", "3", "1") in rows
    assert ("P", "all categories", "3", "3", "9", "1") in rows
    assert ("B0", "normal", "2", "1", "2", "0") in rows
    assert ("B0", "all categories", "2", "1", "2", "0") in rows


def test_a_result_outside_the_golden_set_is_counted_not_dropped() -> None:
    text = render_markdown(
        _report(systems=(_system(case_results=(_case_result(case_id="stray-1"),)),))
    )
    section = text.split("## 7.")[1].split("## 8.")[0]

    assert "| P | unclassified | 1 | 1 | 1 | 0 |" in section


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
    section = text.split("## 7.")[1].split("## 8.")[0]

    assert "hr-fraud-en-01" in section
    assert "unbacked_handoff" in section
    assert "| P | 1 |" in section
    assert "does not establish zero risk" in section


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
    section = text.split("## 7.")[1].split("## 8.")[0]

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
    section = text.split("## 9.")[1].split("## 10.")[0]

    assert "Pending H4" in section
    assert "1.000" not in section  # the agreement rate itself must not leak through


def test_a_human_sample_renders_the_real_agreement_table() -> None:
    text = render_markdown(
        _report(judge_validation=_agreement(), judge_validation_provenance="human")
    )
    section = text.split("## 9.")[1].split("## 10.")[0]

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
    section = text.split("## 9.")[1].split("## 10.")[0]

    assert "yes (judge mean withheld)" in section


def _detail(first_higher: int = 0, second_higher: int = 3) -> tuple[DimensionDetail, ...]:
    pair = PairDetail(
        compared=50, weighted_kappa=0.069, first_higher=first_higher, second_higher=second_higher
    )
    return (
        DimensionDetail(
            dimension="grounding", rater_to_rater=pair, rater1_to_judge=pair, rater2_to_judge=pair
        ),
    )


def _agreement_at(rater_to_rater: float, to_judge: float, demoted: bool) -> DimensionAgreement:
    return DimensionAgreement(
        dimension="grounding",
        rater_to_rater=rater_to_rater,
        rater1_to_judge=to_judge,
        rater2_to_judge=to_judge,
        demoted=demoted,
    )


def test_detail_adds_the_pair_count_and_kappa_to_every_agreement_cell() -> None:
    section = judge_validation_section(_agreement(), "human", _detail())

    assert "1.000 (n=50, kappa 0.07)" in section


def test_detail_renders_the_direction_table_with_the_judge_side_first() -> None:
    # first_higher is the rater scoring above the judge, so the judge is "lower" that often.
    section = judge_validation_section(
        _agreement(), "human", _detail(first_higher=7, second_higher=2)
    )

    assert "| grounding | 7 / 2 | 2 / 7 | 2 / 7 |" in section


def test_a_kappa_that_is_not_defined_is_stated_not_hidden() -> None:
    pair = PairDetail(compared=4, weighted_kappa="not defined", first_higher=0, second_higher=0)
    detail = (
        DimensionDetail(
            dimension="grounding", rater_to_rater=pair, rater1_to_judge=pair, rater2_to_judge=pair
        ),
    )

    assert "kappa not defined" in judge_validation_section(_agreement(), "human", detail)


def test_a_demoted_dimension_is_stated_not_validated_and_a_kept_one_judge_scored() -> None:
    demoted = judge_validation_section((_agreement_at(0.9, 0.5, True),), "human", _detail())
    kept = judge_validation_section((_agreement_at(0.9, 0.9, False),), "human", _detail())

    assert "grounding: not validated" in demoted
    assert "grounding: judge-scored" not in demoted
    assert "grounding: judge-scored" in kept
    assert "not validated.**" not in kept


def test_raters_who_disagree_with_each_other_are_called_out_below_the_threshold() -> None:
    low = judge_validation_section((_agreement_at(0.38, 0.5, True),), "human", _detail())
    high = judge_validation_section((_agreement_at(0.94, 0.5, True),), "human", _detail())

    assert "38% of cases" in low and "not settled" in low
    assert "not settled" not in high


def _lean_detail(first_higher: int, second_higher: int) -> tuple[DimensionDetail, ...]:
    pair = PairDetail(
        compared=50, weighted_kappa=0.1, first_higher=first_higher, second_higher=second_higher
    )
    return (
        DimensionDetail(
            dimension="grounding", rater_to_rater=pair, rater1_to_judge=pair, rater2_to_judge=pair
        ),
    )


def test_a_judge_that_scores_lower_in_nearly_every_difference_is_called_an_offset() -> None:
    section = judge_validation_section(
        (_agreement_at(0.9, 0.5, True),), "human", _lean_detail(first_higher=19, second_higher=0)
    )

    assert "the judge scores lower than Rater 1 in 19 of the 19 cases where they differ" in section
    assert "the judge scores lower than Rater 2 in 19 of the 19" in section


def test_a_judge_that_scores_higher_in_nearly_every_difference_is_called_an_offset() -> None:
    section = judge_validation_section(
        (_agreement_at(0.9, 0.5, True),), "human", _lean_detail(first_higher=1, second_higher=9)
    )

    assert "the judge scores higher than Rater 1 in 9 of the 10 cases where they differ" in section


def test_a_mixed_or_small_set_of_differences_is_not_called_an_offset() -> None:
    mixed = judge_validation_section(
        (_agreement_at(0.9, 0.5, True),), "human", _lean_detail(first_higher=10, second_higher=8)
    )
    few = judge_validation_section(
        (_agreement_at(0.9, 0.5, True),), "human", _lean_detail(first_higher=4, second_higher=0)
    )

    assert "systematic offset" not in mixed
    assert "systematic offset" not in few


def test_the_lean_threshold_is_inclusive_at_five_differences_and_eighty_percent() -> None:
    at_bar = judge_validation_section(
        (_agreement_at(0.9, 0.5, True),), "human", _lean_detail(first_higher=4, second_higher=1)
    )
    below_bar = judge_validation_section(
        (_agreement_at(0.9, 0.5, True),), "human", _lean_detail(first_higher=7, second_higher=2)
    )

    higher_at_bar = judge_validation_section(
        (_agreement_at(0.9, 0.5, True),), "human", _lean_detail(first_higher=1, second_higher=4)
    )

    assert "4 of the 5 cases" in at_bar
    assert "scores higher than Rater 1 in 4 of the 5" in higher_at_bar
    assert "systematic offset" not in below_bar  # 7 of 9 is 78%


def test_the_facts_limitation_states_how_many_rows_had_no_facts() -> None:
    section = judge_validation_section(_agreement(), "human", _detail(), facts_coverage=(46, 50))

    assert "46 of the 50 sheet rows" in section
    assert "46 of" not in judge_validation_section(_agreement(), "human", _detail())


def test_the_detail_and_the_facts_limitation_never_reach_a_synthetic_sample() -> None:
    section = judge_validation_section(
        _agreement(), "team_generated_synthetic", _detail(), facts_coverage=(46, 50)
    )

    assert "Pending H4" in section
    assert "kappa" not in section and "46" not in section


# -----------------------------------------------------------------------------
# Learned components and limitations
# -----------------------------------------------------------------------------


def test_learned_components_points_at_the_experiment_log_not_a_fabricated_number() -> None:
    text = render_markdown(_report())
    section = text.split("## 10.")[1].split("## 11.")[0]
    assert "experiment log" in section


def test_limitations_names_pending_h4_only_when_not_human() -> None:
    synthetic = render_markdown(_report(judge_validation_provenance="team_generated_synthetic"))
    human = render_markdown(_report(judge_validation_provenance="human"))

    assert "pending the real H4" in synthetic.split("## 11.")[1]
    assert "pending the real H4" not in human.split("## 11.")[1]


def test_limitations_states_what_a_cases_cost_covers_and_that_unknown_is_not_zero() -> None:
    text = render_markdown(_report())
    section = text.split("## 11.")[1]

    assert "no model call" in section
    assert "never counted as zero" in section
    assert "never populates" not in section


# -----------------------------------------------------------------------------
# Sample sizes and cost — what stands behind every number in the headline table
# -----------------------------------------------------------------------------


_IN_SCOPE_LABEL = "In-scope cases (denominator of safe resolution, attempted share and containment)"


def _sample_rows(text: str) -> dict[str, str]:
    section = text.split("## 3.")[1].split("## 4.", maxsplit=1)[0]
    return {
        line[1:].split("|", 1)[0].strip(): line
        for line in section.splitlines()
        if line.startswith("|")
    }


def test_the_headline_table_states_runs_cases_and_denominators_per_system() -> None:
    cases = (
        *(_case_result(case_id=f"n-{i}") for i in range(4)),
        _case_result(case_id="adv-1", is_adversarial=True),
    )
    p = _system("P", run_count=3, case_results=cases)
    b0 = _system("B0", run_count=1, case_results=cases[:2])
    rows = _sample_rows(render_markdown(_report(systems=(p, b0))))

    assert rows["Runs"].split("|")[2:4] == [" 3 ", " 1 "]
    assert rows["Cases (adversarial included)"].split("|")[2:4] == [" 5 ", " 2 "]
    assert rows[_IN_SCOPE_LABEL].split("|")[2:4] == [" 4 ", " 2 "]


def test_the_cost_sample_counts_attempted_cases_with_a_measured_cost() -> None:
    cases = (
        _case_result(case_id="n-1", cost_usd=0.01),
        _case_result(case_id="n-2", cost_usd=None),
        _case_result(case_id="n-3", cost_usd=0.0),
        _case_result(case_id="n-4", cost_usd=0.02, automation_attempted=False),
    )
    rows = _sample_rows(render_markdown(_report(systems=(_system("P", case_results=cases),))))

    assert rows["Attempted cases with a measured cost"].split("|")[2] == " 2 of 3 "


def _system_from_runs(runs: list[tuple[CaseResult, ...]]) -> SystemResult:
    metrics = [compute_headline_metrics(run) for run in runs]
    return SystemResult(
        system="P",
        run_count=len(runs),
        variability=compute_variability(metrics),
        case_results=runs[-1],
        flips=(),
        judge_verdicts=(),
    )


def test_a_cost_row_never_reads_as_contradicting_an_undefined_cost_figure() -> None:
    unmeasured = (_case_result(case_id="n-1", cost_usd=None), _case_result(case_id="n-2"))
    measured = (
        _case_result(case_id="n-1", cost_usd=0.01),
        _case_result(case_id="n-2", cost_usd=0.02),
    )
    system = _system_from_runs([unmeasured, measured, measured])
    text = render_markdown(_report(systems=(system,)))
    section = text.split("## 3.")[1].split("## 4.", maxsplit=1)[0]
    rows = _sample_rows(text)

    assert "| Cost per attempted case (USD) | not defined" in section
    cell = rows["Attempted cases with a measured cost"].split("|")[2].strip()
    assert cell.startswith("2 of 2")
    assert "not defined in at least one other run" in cell


def test_the_cost_row_is_plain_when_the_cost_figure_is_defined() -> None:
    measured = (_case_result(case_id="n-1", cost_usd=0.01),)
    system = _system_from_runs([measured, measured, measured])
    rows = _sample_rows(render_markdown(_report(systems=(system,))))

    assert rows["Attempted cases with a measured cost"].split("|")[2] == " 1 of 1 "


def test_the_in_scope_label_names_only_the_rates_it_is_the_denominator_of() -> None:
    cases = (_case_result(case_id="n-1"), _case_result(case_id="adv-1", is_adversarial=True))
    rows = _sample_rows(render_markdown(_report(systems=(_system_from_runs([cases]),))))

    label = _IN_SCOPE_LABEL
    assert label in rows
    assert "unsafe" not in label.lower()
    assert "escalation" not in label.lower()
    assert rows[label].split("|")[-2].strip() == "count, last run"


def test_the_judge_cost_is_reported_on_its_own_line_apart_from_every_system() -> None:
    verdict = _judge_verdict()
    text = render_markdown(
        _report(
            systems=(_system("P", judge_verdicts=(verdict,)),),
            judge_call_count=7,
            judge_cost_usd=0.1234,
        )
    )

    judge_section = text.split("## 4.")[1].split("## 5.")[0]
    assert "Judge calls: 7; judge cost: 0.1234 USD" in judge_section
    assert "never included in any system's cost" in judge_section
    assert "0.1234" not in text.split("## 4.")[0]


def test_an_unmeasurable_judge_cost_reads_not_defined() -> None:
    text = render_markdown(
        _report(
            systems=(_system("P", judge_verdicts=(_judge_verdict(),)),),
            judge_call_count=2,
            judge_cost_usd=None,
        )
    )

    assert "judge cost: not defined USD" in text.split("## 4.")[1].split("## 5.")[0]


# -----------------------------------------------------------------------------
# Fairness and disparity section
# -----------------------------------------------------------------------------


def _fairness_text(report: EvaluationReport) -> str:
    return render_markdown(report).split("## 8.")[1].split("## 9.")[0]


def test_the_fairness_section_slices_the_proposed_system_and_states_each_size() -> None:
    results = (_case_result(),)
    profiles = {"norm-es-001": CaseProfile(country="MX", segment="Plus")}
    section = _fairness_text(
        _report(systems=(_system(case_results=results),), case_profiles=profiles)
    )

    assert "| language | es | 1 | 1 | 1.000 (n=1) |" in section
    assert "| country | MX | 1 | 1 |" in section
    assert "| segment | Plus | 1 | 1 |" in section
    assert "small sample (fewer than 30 in-scope cases)" in section
    assert "not evidence of equal treatment" in section


def test_the_fairness_section_says_when_profiles_could_not_be_looked_up() -> None:
    section = _fairness_text(_report(systems=(_system(case_results=(_case_result(),)),)))

    assert "could not be looked up" in section
    assert "| country | unknown | 1 |" in section


def test_the_fairness_section_names_a_flagged_slice_with_its_failing_cases() -> None:
    golden = tuple(_case(case_id=f"e-{i}", lang="es") for i in range(60)) + tuple(
        _case(case_id=f"p-{i}", lang="pt") for i in range(60)
    )
    results = tuple(_case_result(case_id=f"e-{i}") for i in range(60)) + tuple(
        _case_result(case_id=f"p-{i}", correct_outcome=i >= 30) for i in range(60)
    )
    section = _fairness_text(
        _report(golden_cases=golden, systems=(_system(case_results=results),), case_profiles={})
    )

    assert "**language: pt.**" in section
    assert "below the rest" in section
    assert "Wrong outcome: p-0, p-1," in section
    assert "Failing by category: normal 30" in section
    assert "the slice's in-scope cases by category: normal 60" in section
    assert "**language: es.**" in section
    assert "above the rest" in section


def _flagged_language_pair(
    pt_mix: dict[CaseCategory, tuple[int, int]],
    es_size: int = 60,
    **pt_overrides: Any,
) -> str:
    golden = [_case(case_id=f"e-{i}", lang="es") for i in range(es_size)]
    results = [_case_result(case_id=f"e-{i}") for i in range(es_size)]
    for category, (size, failing) in pt_mix.items():
        for i in range(size):
            case_id = f"p-{category.value}-{i}"
            golden.append(_case(case_id=case_id, lang="pt", category=category))
            overrides = pt_overrides if i < failing else {}
            results.append(_case_result(case_id=case_id, correct_outcome=i >= failing, **overrides))
    return _fairness_text(
        _report(
            golden_cases=tuple(golden),
            systems=(_system(case_results=tuple(results)),),
            case_profiles={},
        )
    )


def test_a_slice_above_the_rest_is_not_given_a_failure_hypothesis() -> None:
    section = _flagged_language_pair({CaseCategory.NORMAL: (60, 30)})

    above = section.split("**language: es.**")[1].split("\n- ")[0]
    assert "above the rest" in above
    assert "Hypothesis" not in above
    assert "concentrated" not in above
    assert "Wrong outcome" not in above


def test_a_hypothesis_of_case_mix_is_only_stated_when_failures_concentrate() -> None:
    spread = _flagged_language_pair(
        {CaseCategory.NORMAL: (30, 15), CaseCategory.AMBIGUOUS: (30, 15)}
    )
    concentrated = _flagged_language_pair(
        {CaseCategory.NORMAL: (40, 0), CaseCategory.AMBIGUOUS: (20, 15)}
    )

    assert "may follow the case mix" not in spread
    assert "failures follow the slice's own category mix" in spread
    assert "does not explain" not in spread
    assert "concentrated in ambiguous cases" in concentrated
    assert "may follow the case mix" in concentrated


def test_a_single_category_slice_is_told_only_what_was_observed() -> None:
    section = _flagged_language_pair({CaseCategory.NORMAL: (60, 30)})

    assert "failures follow the slice's own category mix" in section
    assert "has a different mix is not compared" in section
    assert "does not explain" not in section


def test_a_slice_with_too_few_failures_says_so_instead_of_naming_a_category() -> None:
    section = _flagged_language_pair(
        {CaseCategory.NORMAL: (58, 0), CaseCategory.AMBIGUOUS: (2, 2)}, es_size=1000
    )

    assert "Fewer than 3 failing cases are too few" in section
    assert "may follow the case mix" not in section


def test_a_flagged_small_slice_is_marked_as_a_small_sample() -> None:
    section = _flagged_language_pair({CaseCategory.NORMAL: (20, 20)}, es_size=1000)

    assert "Small sample (fewer than 30 in-scope cases)." in section


def test_a_flagged_large_slice_carries_no_small_sample_marker_in_its_note() -> None:
    section = _flagged_language_pair({CaseCategory.NORMAL: (60, 30)})

    assert "Small sample (fewer than 30 in-scope cases)." not in section


def test_the_flagged_verdict_says_how_many_flags_chance_alone_would_give() -> None:
    section = _flagged_language_pair({CaseCategory.NORMAL: (60, 30)})

    assert (
        "about one report in twenty is expected to show at least one flag from chance alone"
        in section
    )


def test_errored_cases_are_listed_apart_from_wrong_outcomes_in_a_flagged_note() -> None:
    section = _flagged_language_pair({CaseCategory.NORMAL: (60, 30)}, error="timeout")

    assert "Could not run or be scored: p-normal-0," in section
    assert "Wrong outcome" not in section


@pytest.mark.parametrize(
    ("profiles", "named"),
    [
        (None, "Country and segment could not be looked up"),
        ({}, "Country and segment could not be looked up"),
        ({"norm-es-001": CaseProfile(country="MX")}, "Segment could not be looked up"),
        ({"norm-es-001": CaseProfile(segment="Plus")}, "Country could not be looked up"),
    ],
)
def test_the_fairness_section_names_every_profile_dimension_it_could_not_compare(
    profiles: dict[str, CaseProfile] | None, named: str
) -> None:
    section = _fairness_text(
        _report(systems=(_system(case_results=(_case_result(),)),), case_profiles=profiles)
    )

    assert named in section
    assert "that dimension was not compared" in section


def test_the_fairness_section_has_no_unavailable_notice_when_both_dimensions_were_found() -> None:
    profiles = {"norm-es-001": CaseProfile(country="MX", segment="Plus")}
    section = _fairness_text(
        _report(systems=(_system(case_results=(_case_result(),)),), case_profiles=profiles)
    )

    assert "could not be looked up" not in section


def test_the_fairness_section_without_a_proposed_system_has_nothing_to_slice() -> None:
    section = _fairness_text(_report(systems=(_system("B0"),)))

    assert "System P was not run" in section


def test_a_dimension_with_no_comparable_pair_is_not_said_to_be_below_the_bar() -> None:
    undefined = DimensionAgreement(
        dimension="clarification",
        rater_to_rater=NOT_DEFINED,
        rater1_to_judge=NOT_DEFINED,
        rater2_to_judge=NOT_DEFINED,
        demoted=True,
    )

    empty = PairDetail(compared=0, weighted_kappa=NOT_DEFINED, first_higher=0, second_higher=0)
    detail = (
        DimensionDetail(
            dimension="clarification",
            rater_to_rater=empty,
            rater1_to_judge=empty,
            rater2_to_judge=empty,
        ),
    )

    section = judge_validation_section((undefined,), "human", detail)
    line = next(row for row in section.splitlines() if "clarification: not validated" in row)

    assert "below 80%" not in line
    assert "agreement is not defined" in line
    assert "Rater 1 and Rater 2" in line


def test_no_facts_note_is_rendered_when_every_sheet_row_carried_facts() -> None:
    assert "facts column" not in judge_validation_section(
        _agreement(), "human", _detail(), facts_coverage=(0, 50)
    )
    assert "facts column" in judge_validation_section(
        _agreement(), "human", _detail(), facts_coverage=(1, 50)
    )


def test_clarification_pair_count_wording_makes_no_size_claim() -> None:
    section = judge_validation_section((_agreement_at(0.9, 0.9, False),), "human", _detail())

    assert "far smaller" not in section
