"""
H4 Judge Validation Tests
===========================

Component: ``evals.h4_judge_validation``. Hermetic throughout: the judge is always ``FakeLlm``, and
every rater sheet is a small, committed fixture (``tests/fixtures/h4_case_sheet_rater{1,2}.csv``),
never the real, private returned sheets.
"""

from __future__ import annotations

# Standard libraries
import logging
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Third-party libraries
import pytest

# Local modules
from app.llm.client import FakeLlm
from contracts.service_v1.envelope import Intent
from evals import h4_judge_validation
from evals.facts import NO_KNOWN_FACTS
from evals.h4_judge_validation import (
    RaterCaseRow,
    _as_rater_scores,
    _check_roles,
    _check_same_prepared_packet,
    _facts_coverage,
    apply_real_judge_validation,
    case_scores_csv,
    load_rater_sheet,
    main,
    regenerate_report,
    score_with_judge,
)
from evals.judge import LlmJudge
from evals.judge_validation import (
    DimensionAgreement,
    DimensionDetail,
    HumanMean,
    compute_agreement,
    compute_detail,
    compute_human_means,
)
from evals.metrics import CaseResult, compute_headline_metrics
from evals.models import Case, CaseCategory
from evals.repeated_runs import compute_variability
from evals.report import (
    EvaluationReport,
    SystemResult,
    Versions,
    render_markdown,
    withhold_demoted_judge_means,
)

_MODEL = "claude-sonnet-5"
_FIXTURES = Path(__file__).parent / "fixtures"
_RATER_1_CSV = _FIXTURES / "h4_case_sheet_rater1.csv"
_RATER_2_CSV = _FIXTURES / "h4_case_sheet_rater2.csv"


def _row(**overrides: Any) -> RaterCaseRow:
    values: dict[str, Any] = {
        "case_id": "J-01",
        "language": "es",
        "category": "normal",
        "user_turns": ("No reconozco un cargo.",),
        "system_replies": ("Su disputa fue presentada.",),
        "facts_and_sources": "Case CASE-001 filed.",
        "role": "Rater 1",
        "grounding": 2,
        "language_quality": 2,
        "clarification": None,
        "comment": "",
    }
    return RaterCaseRow(**{**values, **overrides})


# -----------------------------------------------------------------------------
# load_rater_sheet
# -----------------------------------------------------------------------------


def test_load_rater_sheet_parses_every_column() -> None:
    rows = load_rater_sheet(_RATER_1_CSV)
    assert [row.case_id for row in rows] == ["J-01", "J-02", "J-03"]

    j01 = rows[0]
    assert j01.language == "es"
    assert j01.category == "normal"
    assert j01.user_turns == (
        "No reconozco un cargo en mi tarjeta.",
        "Sí, quiero presentar la disputa.",
    )
    assert j01.system_replies == (
        "Su disputa fue presentada con el caso CASE-001.",
        "La respuesta esperada es antes del 2026-10-01.",
    )
    assert j01.role == "Rater 1"
    assert j01.grounding == 2
    assert j01.clarification is None  # "NA" in the sheet

    j02 = rows[1]
    assert j02.comment == "Minor added phrase not in the record"

    j03 = rows[2]
    assert j03.clarification == 2  # scored, not NA — an ambiguous-category case


def test_load_rater_sheet_rejects_an_out_of_range_score(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "case_id,language,category,user_turns,system_replies,facts_and_sources,role,"
        "grounding,language_quality,clarification,comment\n"
        "J-99,es,normal,x,y,z,Rater 1,3,2,NA,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="grounding must be 0, 1 or 2"):
        load_rater_sheet(bad)


# -----------------------------------------------------------------------------
# _check_same_prepared_packet, via score_with_judge's own caller contract
# -----------------------------------------------------------------------------


def test_check_same_prepared_packet_passes_for_the_real_fixture_pair() -> None:
    """The module's own consistency check, exercised the same way main() exercises it, against
    the two real fixture files."""
    rater1 = load_rater_sheet(_RATER_1_CSV)
    rater2 = load_rater_sheet(_RATER_2_CSV)
    _check_same_prepared_packet(rater1, rater2)  # must not raise


def test_check_same_prepared_packet_raises_on_mismatched_facts() -> None:
    rater1 = (_row(case_id="J-01", facts_and_sources="Case CASE-001 filed."),)
    rater2 = (_row(case_id="J-01", facts_and_sources="Case CASE-999 filed instead."),)
    with pytest.raises(ValueError, match="disagree on"):
        _check_same_prepared_packet(rater1, rater2)


def test_check_same_prepared_packet_raises_on_different_case_ids() -> None:
    rater1 = (_row(case_id="J-01"), _row(case_id="J-02"))
    rater2 = (_row(case_id="J-01"),)
    with pytest.raises(ValueError, match="cover different case ids"):
        _check_same_prepared_packet(rater1, rater2)


# -----------------------------------------------------------------------------
# score_with_judge
# -----------------------------------------------------------------------------


def test_score_with_judge_calls_once_per_row_with_the_sheets_own_data() -> None:
    rows = load_rater_sheet(_RATER_1_CSV)
    llm = FakeLlm(
        responses=[
            {"grounding": 2, "language_quality": 2, "rationale": "Grounded, clear."},
            {"grounding": 1, "language_quality": 1, "rationale": "Missing case number."},
            {"grounding": 2, "language_quality": 2, "clarification": 2, "rationale": "Good ask."},
        ]
    )
    judge = LlmJudge(llm, model=_MODEL)

    verdicts = score_with_judge(rows, judge)

    assert [v.case_id for v in verdicts] == ["J-01", "J-02", "J-03"]
    assert verdicts[0].clarification is None
    assert verdicts[2].clarification == 2
    # The judge was given the sheet's own captured replies/facts, not re-derived from anywhere.
    sent = llm.requests[0]
    assert "Su disputa fue presentada con el caso CASE-001" in sent.user_text
    assert "Case CASE-001 filed for transaction TRX-001" in sent.user_text


# -----------------------------------------------------------------------------
# End-to-end agreement, against the real fixture pair
# -----------------------------------------------------------------------------


def test_full_flow_computes_agreement_and_flags_the_demoted_dimension() -> None:
    """grounding and clarification both stay above threshold; language_quality is deliberately
    constructed to fall below it (rater 1 disagrees with the judge on J-02, rater 2 does not) —
    the fixture pair's whole point is to exercise the demotion path for real, not just the happy
    path every other dimension already covers."""
    rater1 = load_rater_sheet(_RATER_1_CSV)
    rater2 = load_rater_sheet(_RATER_2_CSV)
    llm = FakeLlm(
        responses=[
            {"grounding": 2, "language_quality": 2, "rationale": "J-01: grounded, clear."},
            {"grounding": 1, "language_quality": 1, "rationale": "J-02: missing case number."},
            {"grounding": 2, "language_quality": 2, "clarification": 2, "rationale": "J-03: good."},
        ]
    )
    judge = LlmJudge(llm, model=_MODEL)
    judge_verdicts = score_with_judge(rater1, judge)

    agreement = compute_agreement(
        _as_rater_scores(rater1), _as_rater_scores(rater2), judge_verdicts
    )
    by_dimension = {entry.dimension: entry for entry in agreement}

    assert by_dimension["grounding"].rater_to_rater == 1.0
    assert by_dimension["grounding"].demoted is False

    lq = by_dimension["language_quality"]
    assert lq.rater_to_rater == pytest.approx(2 / 3)
    assert lq.rater1_to_judge == pytest.approx(2 / 3)  # below 0.8
    assert lq.rater2_to_judge == 1.0
    assert lq.demoted is True

    clarification = by_dimension["clarification"]
    assert clarification.rater_to_rater == 1.0  # only J-03 is comparable
    assert clarification.demoted is False


# -----------------------------------------------------------------------------
# apply_real_judge_validation
# -----------------------------------------------------------------------------


def _minimal_report(**overrides: Any) -> EvaluationReport:
    headline = compute_variability(
        [
            compute_headline_metrics(
                (
                    CaseResult(
                        case_id="C1",
                        is_adversarial=False,
                        expected_escalation=False,
                        observed_escalation=False,
                        automation_attempted=True,
                        correct_outcome=True,
                    ),
                )
            )
        ]
    )
    system = SystemResult(
        system="P",
        run_count=1,
        variability=headline,
        case_results=(),
        flips=(),
        judge_verdicts=(),
    )
    versions = Versions(
        nlu_model=_MODEL,
        render_model=_MODEL,
        judge_model=_MODEL,
        nlu_prompt_version="1",
        render_prompt_version="1",
        judge_prompt_version="1",
        policy_version="2",
        git_sha="abc1234",
    )
    case = Case(
        case_id="norm-es-001",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-1",
        user_turns=("¿Cuánto tiempo tengo?",),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="filing-windows",
    )
    defaults: dict[str, Any] = {
        "versions": versions,
        "golden_cases": (case,),
        "systems": (system,),
        "judge_validation": (),
        "judge_validation_provenance": "team_generated_synthetic",
        "reference_date": "2026-06-18",
        "reference_date_source": "seeded data",
        "bank_timezone": "America/Bogota",
    }
    return EvaluationReport(**{**defaults, **overrides})


def test_apply_real_judge_validation_replaces_the_validation_and_drops_the_stale_bullet() -> None:
    before_report = _minimal_report()
    before_text = render_markdown(before_report)
    assert "Pending H4" in before_text
    assert "pending the real H4 human sample" in before_text

    rater1 = load_rater_sheet(_RATER_1_CSV)
    rater2 = load_rater_sheet(_RATER_2_CSV)
    llm = FakeLlm(
        responses=[
            {"grounding": 2, "language_quality": 2, "rationale": "ok"},
            {"grounding": 1, "language_quality": 1, "rationale": "ok"},
            {"grounding": 2, "language_quality": 2, "clarification": 2, "rationale": "ok"},
        ]
    )
    judge = LlmJudge(llm, model=_MODEL)
    agreement = compute_agreement(
        _as_rater_scores(rater1), _as_rater_scores(rater2), score_with_judge(rater1, judge)
    )

    after_text = apply_real_judge_validation(
        before_text,
        agreement,
        human_means=compute_human_means(_as_rater_scores(rater1), _as_rater_scores(rater2)),
    )

    assert "Pending H4" not in after_text
    assert "pending the real H4 human sample" not in after_text
    assert "Judge-validation sample provenance: `human`." in after_text
    assert "yes (judge mean withheld)" in after_text  # language_quality's own demotion

    # Every other section is untouched: the same "after" text, rendered directly from a report
    # that already carried the real agreement and human provenance, matches exactly.
    same_report = _minimal_report(judge_validation=agreement, judge_validation_provenance="human")
    assert after_text == render_markdown(same_report)


def test_apply_real_judge_validation_raises_on_an_unrecognized_report_shape() -> None:
    with pytest.raises(ValueError, match="cannot recognize"):
        apply_real_judge_validation("not a real report at all", agreement=(), human_means=())


def test_apply_real_judge_validation_refuses_a_report_with_no_section_after_the_validation() -> (
    None
):
    text = render_markdown(_minimal_report())
    start = text.index("## 9. Judge validation")
    truncated = text[:start] + "## 9. Judge validation\n\nbody only, nothing after it\n"
    with pytest.raises(ValueError, match="cannot recognize"):
        apply_real_judge_validation(truncated, agreement=(), human_means=())


def test_apply_real_judge_validation_refuses_a_report_with_no_validation_section() -> None:
    text = render_markdown(_minimal_report())
    renamed = text.replace("## 9. Judge validation", "## 9. Rater agreement")
    with pytest.raises(ValueError, match="cannot recognize"):
        apply_real_judge_validation(renamed, agreement=(), human_means=())


def test_apply_real_judge_validation_finds_the_section_by_title_not_number() -> None:
    text = render_markdown(_minimal_report())
    renumbered = text.replace("## 9. Judge validation", "## 4. Judge validation")
    patched = apply_real_judge_validation(renumbered, agreement=(), human_means=())
    assert "## 4. Judge validation" in patched
    assert "Pending H4" not in patched


def test_apply_real_judge_validation_drops_the_bullet_without_a_trailing_newline() -> None:
    text = render_markdown(_minimal_report())
    assert text.endswith("\n")
    bullet = "- The judge-validation section is pending the real H4 human sample; see that "
    assert bullet in text
    # The bullet is the last line of the report, with no newline after it.
    head = text.split(bullet)[0]
    trimmed = head + bullet + "section for detail."
    assert "pending the real H4 human sample" not in apply_real_judge_validation(
        trimmed, (), human_means=()
    )


# -----------------------------------------------------------------------------
# Sheet parsing and integrity
# -----------------------------------------------------------------------------

_HEADER = (
    "case_id,language,category,user_turns,system_replies,facts_and_sources,role,"
    "grounding,language_quality,clarification,comment\n"
)


def _write_sheet(tmp_path: Path, body: str, *, header: str = _HEADER, name: str = "s.csv") -> Path:
    path = tmp_path / name
    path.write_text(header + body, encoding="utf-8")
    return path


def test_load_rater_sheet_rejects_a_non_integer_score(tmp_path: Path) -> None:
    sheet = _write_sheet(tmp_path, "J-1,es,normal,x,y,z,Rater 1,two,2,NA,\n")
    with pytest.raises(ValueError, match="grounding must be 0, 1 or 2"):
        load_rater_sheet(sheet)


@pytest.mark.parametrize("marker", ["NA", "na", "Na", "", "  "])
def test_clarification_not_applicable_is_none_never_zero(tmp_path: Path, marker: str) -> None:
    sheet = _write_sheet(tmp_path, f"J-1,es,normal,x,y,z,Rater 1,2,2,{marker},\n")
    assert load_rater_sheet(sheet)[0].clarification is None


def test_clarification_zero_is_a_score(tmp_path: Path) -> None:
    sheet = _write_sheet(tmp_path, "J-1,es,normal,x,y,z,Rater 1,2,2,0,\n")
    assert load_rater_sheet(sheet)[0].clarification == 0


def test_clarification_out_of_range_is_rejected(tmp_path: Path) -> None:
    sheet = _write_sheet(tmp_path, "J-1,es,normal,x,y,z,Rater 1,2,2,3,\n")
    with pytest.raises(ValueError, match="clarification must be 0, 1 or 2"):
        load_rater_sheet(sheet)


def test_load_rater_sheet_accepts_a_byte_order_mark(tmp_path: Path) -> None:
    sheet = tmp_path / "bom.csv"
    sheet.write_bytes(
        b"\xef\xbb\xbf" + (_HEADER + "J-1,es,normal,x,y,z,Rater 1,2,2,NA,\n").encode("utf-8")
    )
    assert load_rater_sheet(sheet)[0].case_id == "J-1"


def test_load_rater_sheet_rejects_a_semicolon_delimited_file(tmp_path: Path) -> None:
    sheet = _write_sheet(
        tmp_path, "J-1;es;normal;x;y;z;Rater 1;2;2;NA;\n", header=_HEADER.replace(",", ";")
    )
    with pytest.raises(ValueError, match="missing column"):
        load_rater_sheet(sheet)


def test_load_rater_sheet_rejects_a_short_row(tmp_path: Path) -> None:
    sheet = _write_sheet(tmp_path, "J-1,es,normal,x,y,z,Rater 1,2\n")
    with pytest.raises(ValueError, match="does not have 11 columns"):
        load_rater_sheet(sheet)


def test_load_rater_sheet_rejects_an_unknown_role(tmp_path: Path) -> None:
    sheet = _write_sheet(tmp_path, "J-1,es,normal,x,y,z,Rater 3,2,2,NA,\n")
    with pytest.raises(ValueError, match="role must be one of"):
        load_rater_sheet(sheet)


def test_load_rater_sheet_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    sheet = _write_sheet(
        tmp_path, "J-1,es,normal,x,y,z,Rater 1,2,2,NA,\nJ-1,es,normal,x,y,z,Rater 1,1,1,NA,\n"
    )
    with pytest.raises(ValueError, match="appears more than once"):
        load_rater_sheet(sheet)


def test_load_rater_sheet_rejects_a_sheet_with_no_rows(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no rows"):
        load_rater_sheet(_write_sheet(tmp_path, ""))


def test_a_bare_pipe_inside_a_turn_is_not_a_turn_boundary(tmp_path: Path) -> None:
    sheet = _write_sheet(tmp_path, 'J-1,es,normal,"a|b | c",y,z,Rater 1,2,2,NA,\n')
    assert load_rater_sheet(sheet)[0].user_turns == ("a|b", "c")


@pytest.mark.parametrize(
    "column", ["language", "category", "user_turns", "system_replies", "facts_and_sources"]
)
def test_packet_check_compares_every_shared_column(column: str) -> None:
    value: Any = ("different",) if column in ("user_turns", "system_replies") else "different"
    with pytest.raises(ValueError, match="disagree on"):
        _check_same_prepared_packet((_row(),), (_row(**{column: value}),))


def test_roles_check_refuses_the_same_sheet_passed_twice() -> None:
    rows = load_rater_sheet(_RATER_1_CSV)
    with pytest.raises(ValueError, match="Rater 2 sheet carries rows with role"):
        _check_roles(rows, rows)


def test_roles_check_passes_for_the_fixture_pair() -> None:
    _check_roles(load_rater_sheet(_RATER_1_CSV), load_rater_sheet(_RATER_2_CSV))


# -----------------------------------------------------------------------------
# regenerate_report and main
# -----------------------------------------------------------------------------

_JUDGE_RESPONSES: list[dict[str, Any]] = [
    {"grounding": 2, "language_quality": 2, "rationale": "ok"},
    {"grounding": 1, "language_quality": 1, "rationale": "ok"},
    {"grounding": 2, "language_quality": 2, "clarification": 2, "rationale": "ok"},
]


def _stage(tmp_path: Path) -> tuple[Path, Path, Path]:
    rater1 = tmp_path / "r1.csv"
    rater2 = tmp_path / "r2.csv"
    shutil.copy(_RATER_1_CSV, rater1)
    shutil.copy(_RATER_2_CSV, rater2)
    report = tmp_path / "evaluation.md"
    report.write_text(render_markdown(_minimal_report()), encoding="utf-8")
    return rater1, rater2, report


def test_regenerate_report_patches_the_file_logs_agreement_and_calls_the_judge_per_case(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rater1, rater2, report = _stage(tmp_path)
    llm = FakeLlm(responses=_JUDGE_RESPONSES)

    with caplog.at_level(logging.INFO, logger="evals.h4_judge_validation"):
        agreement = regenerate_report(rater1, rater2, report, LlmJudge(llm, model=_MODEL))

    verdicts = score_with_judge(
        load_rater_sheet(rater1), LlmJudge(FakeLlm(_JUDGE_RESPONSES), model=_MODEL)
    )
    rater_scores_1 = _as_rater_scores(load_rater_sheet(rater1))
    rater_scores_2 = _as_rater_scores(load_rater_sheet(rater2))
    expected = apply_real_judge_validation(
        render_markdown(_minimal_report()),
        agreement,
        compute_detail(rater_scores_1, rater_scores_2, verdicts),
        _facts_coverage(load_rater_sheet(rater1)),
        human_means=compute_human_means(rater_scores_1, rater_scores_2),
    )
    assert report.read_text(encoding="utf-8") == expected
    assert "kappa" in expected  # the detail reaches the committed text, not only the table
    language_quality = {row.dimension: row for row in agreement}["language_quality"]
    # Judge scores 2, 1, 2; rater 1 scored 2, 2, 2 (agreement 2/3); rater 2 scored 2, 1, 2 (1.0).
    assert language_quality.rater1_to_judge == pytest.approx(2 / 3)
    assert language_quality.rater2_to_judge == pytest.approx(1.0)
    assert len(llm.requests) == 3
    assert not list(tmp_path.glob(".evaluation.md.*"))  # no temporary file left behind
    assert "judge_validation_report_updated" in caplog.text
    assert "dimension=language_quality" in caplog.text
    assert "demoted=True" in caplog.text
    assert "rater1_to_judge=0.6666666666666666 rater2_to_judge=1.0" in caplog.text
    assert "CASE-001" not in caplog.text  # no sheet text in the logs
    assert "No reconozco" not in caplog.text


def test_regenerate_report_withholds_the_demoted_judge_means_in_the_written_file(
    tmp_path: Path,
) -> None:
    rater1, rater2, report = _stage(tmp_path)
    base = _minimal_report()
    verdicts = score_with_judge(
        load_rater_sheet(rater1), LlmJudge(FakeLlm(_JUDGE_RESPONSES), model=_MODEL)
    )
    judged = replace(base.systems[0], judge_verdicts=verdicts)
    report.write_text(render_markdown(replace(base, systems=(judged,))), encoding="utf-8")

    regenerate_report(rater1, rater2, report, LlmJudge(FakeLlm(_JUDGE_RESPONSES), model=_MODEL))

    written = report.read_text(encoding="utf-8")
    assert "not reportable by the judge" in written
    assert "**Withheld judge means.**" in written


def test_regenerate_report_leaves_an_unrecognized_report_untouched_before_any_judge_call(
    tmp_path: Path,
) -> None:
    rater1, rater2, report = _stage(tmp_path)
    report.write_text("not a real report", encoding="utf-8")
    llm = FakeLlm(responses=_JUDGE_RESPONSES)

    with pytest.raises(ValueError, match="cannot recognize"):
        regenerate_report(rater1, rater2, report, LlmJudge(llm, model=_MODEL))

    assert report.read_text(encoding="utf-8") == "not a real report"
    assert llm.requests == []


def test_regenerate_report_refuses_the_same_sheet_twice_before_any_judge_call(
    tmp_path: Path,
) -> None:
    rater1, _, report = _stage(tmp_path)
    before = report.read_text(encoding="utf-8")
    llm = FakeLlm(responses=_JUDGE_RESPONSES)

    with pytest.raises(ValueError, match="Rater 2 sheet"):
        regenerate_report(rater1, rater1, report, LlmJudge(llm, model=_MODEL))

    assert llm.requests == []
    assert report.read_text(encoding="utf-8") == before


def test_main_wires_the_settings_judge_and_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rater1, rater2, report = _stage(tmp_path)
    llm = FakeLlm(responses=_JUDGE_RESPONSES)
    monkeypatch.setattr(
        h4_judge_validation,
        "load_settings",
        lambda: SimpleNamespace(require_anthropic_key=lambda: "test-key", judge_model=_MODEL),
    )
    monkeypatch.setattr(h4_judge_validation, "AnthropicLlmClient", lambda key: llm)

    status = main(["--rater1", str(rater1), "--rater2", str(rater2), "--report", str(report)])

    assert status == 0
    assert len(llm.requests) == 3
    assert "Judge-validation sample provenance: `human`." in report.read_text(encoding="utf-8")


def test_main_requires_both_rater_paths() -> None:
    with pytest.raises(SystemExit):
        main(["--report", "x.md"])


def test_a_failed_replace_leaves_the_report_and_the_directory_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "evaluation.md"
    target.write_text("original", encoding="utf-8")

    def refuse(self: Path, destination: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        h4_judge_validation._write_atomically(target, "new")

    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "original"
    assert [p.name for p in tmp_path.iterdir()] == ["evaluation.md"]


# -----------------------------------------------------------------------------
# Detail, facts coverage and the per-case scores file
# -----------------------------------------------------------------------------


def test_facts_coverage_counts_the_rows_that_state_no_facts_are_on_record() -> None:
    rows = [
        _row(case_id="J-01", facts_and_sources=NO_KNOWN_FACTS),
        _row(case_id="J-02", facts_and_sources="Case CASE-002 filed."),
        _row(case_id="J-03", facts_and_sources=NO_KNOWN_FACTS),
    ]

    assert _facts_coverage(rows) == (2, 3)


def test_facts_coverage_does_not_count_a_row_that_only_mentions_the_sentinel() -> None:
    rows = [_row(facts_and_sources=NO_KNOWN_FACTS + " Plus a policy section.")]

    assert _facts_coverage(rows) == (0, 1)


def test_the_patched_section_carries_the_decision_direction_table_and_facts_limitation() -> None:
    rater1 = load_rater_sheet(_RATER_1_CSV)
    rater2 = load_rater_sheet(_RATER_2_CSV)
    verdicts = score_with_judge(rater1, LlmJudge(FakeLlm(_JUDGE_RESPONSES), model=_MODEL))
    scores_1, scores_2 = _as_rater_scores(rater1), _as_rater_scores(rater2)
    agreement = compute_agreement(scores_1, scores_2, verdicts)
    detail = compute_detail(scores_1, scores_2, verdicts)

    patched = apply_real_judge_validation(
        render_markdown(_minimal_report()),
        agreement,
        detail,
        (46, 50),
        human_means=compute_human_means(scores_1, scores_2),
    )

    assert "Decision per dimension" in patched
    assert "language_quality: not validated" in patched
    assert "Judge higher / lower than Rater 1" in patched
    assert "46 of the 50 sheet rows" in patched


def test_case_scores_csv_has_one_row_per_case_with_every_source_and_no_text() -> None:
    rater1 = load_rater_sheet(_RATER_1_CSV)
    rater2 = load_rater_sheet(_RATER_2_CSV)
    verdicts = score_with_judge(rater1, LlmJudge(FakeLlm(_JUDGE_RESPONSES), model=_MODEL))

    text = case_scores_csv(rater1, rater2, verdicts)
    lines = text.splitlines()

    assert lines[0] == (
        "case_id,language,category,"
        "grounding_rater1,grounding_rater2,grounding_judge,"
        "language_quality_rater1,language_quality_rater2,language_quality_judge,"
        "clarification_rater1,clarification_rater2,clarification_judge"
    )
    assert len(lines) == 1 + len(rater1)
    first = lines[1].split(",")
    assert first[0] == rater1[0].case_id
    assert first[3:6] == [
        str(rater1[0].grounding),
        str(rater2[0].grounding),
        str(verdicts[0].grounding),
    ]
    assert "No reconozco" not in text  # no conversation text
    assert all(row.comment not in text for row in rater1 if row.comment)


def test_case_scores_csv_leaves_a_clarification_that_was_not_asked_blank() -> None:
    row_1 = _row(case_id="J-01", clarification=None)
    row_2 = _row(case_id="J-01", role="Rater 2", clarification=None)
    verdict = LlmJudge(
        FakeLlm([{"grounding": 2, "language_quality": 2, "rationale": "ok"}]), model=_MODEL
    ).score(
        "J-01",
        language="es",
        user_turns=row_1.user_turns,
        system_replies=row_1.system_replies,
        facts_and_sources=row_1.facts_and_sources,
    )

    line = case_scores_csv([row_1], [row_2], [verdict]).splitlines()[1]

    assert line.endswith(",,,")


def test_regenerate_report_writes_the_cases_file_beside_the_report(tmp_path: Path) -> None:
    rater1, rater2, report = _stage(tmp_path)

    regenerate_report(rater1, rater2, report, LlmJudge(FakeLlm(_JUDGE_RESPONSES), model=_MODEL))

    written = (tmp_path / "judge-validation-cases.csv").read_text(encoding="utf-8")
    assert written.splitlines()[0].startswith("case_id,language,category")
    assert not list(tmp_path.glob(".judge-validation-cases.csv.*"))


def test_regenerate_report_writes_the_cases_file_where_it_is_told(tmp_path: Path) -> None:
    rater1, rater2, report = _stage(tmp_path)
    elsewhere = tmp_path / "scores.csv"

    regenerate_report(
        rater1, rater2, report, LlmJudge(FakeLlm(_JUDGE_RESPONSES), model=_MODEL), elsewhere
    )

    assert elsewhere.exists()
    assert not (tmp_path / "judge-validation-cases.csv").exists()


def test_case_scores_csv_puts_each_source_in_its_own_column() -> None:
    row_1 = _row(case_id="J-01", grounding=0, language_quality=1, clarification=2)
    row_2 = _row(case_id="J-01", role="Rater 2", grounding=1, language_quality=2, clarification=0)
    verdict = LlmJudge(
        FakeLlm([{"grounding": 2, "language_quality": 0, "clarification": 1, "rationale": "ok"}]),
        model=_MODEL,
    ).score(
        "J-01",
        language="es",
        user_turns=row_1.user_turns,
        system_replies=row_1.system_replies,
        facts_and_sources=row_1.facts_and_sources,
    )

    line = case_scores_csv([row_1], [row_2], [verdict]).splitlines()[1]

    assert line == "J-01,es,normal,0,1,2,1,2,0,2,0,1"


@dataclass(frozen=True)
class _JudgedReport:
    markdown: str
    agreement: tuple[DimensionAgreement, ...]
    detail: tuple[DimensionDetail, ...]
    human_means: tuple[HumanMean, ...]


def _judged_report_and_inputs() -> _JudgedReport:
    rater1 = load_rater_sheet(_RATER_1_CSV)
    rater2 = load_rater_sheet(_RATER_2_CSV)
    verdicts = score_with_judge(rater1, LlmJudge(FakeLlm(_JUDGE_RESPONSES), model=_MODEL))
    scores_1, scores_2 = _as_rater_scores(rater1), _as_rater_scores(rater2)
    base = _minimal_report()
    judged = replace(base.systems[0], judge_verdicts=verdicts)
    return _JudgedReport(
        markdown=render_markdown(replace(base, systems=(judged,))),
        agreement=compute_agreement(scores_1, scores_2, verdicts),
        detail=compute_detail(scores_1, scores_2, verdicts),
        human_means=compute_human_means(scores_1, scores_2),
    )


def test_a_demoted_dimension_shows_no_judge_mean_in_the_patched_quality_table() -> None:
    judged = _judged_report_and_inputs()
    markdown, agreement, detail, human_means = (
        judged.markdown,
        judged.agreement,
        judged.detail,
        judged.human_means,
    )
    before_row = next(
        line
        for line in markdown.split("## 4.")[1].split("## 5.")[0].splitlines()
        if line.startswith("| P |")
    )
    demoted = [entry.dimension for entry in agreement if entry.demoted]
    assert demoted

    patched = apply_real_judge_validation(markdown, agreement, detail, human_means=human_means)

    section = patched.split("## 4.")[1].split("## 5.")[0]
    row = next(line for line in section.splitlines() if line.startswith("| P |"))
    assert row.count("not reportable by the judge") == len(demoted)
    columns = {"grounding": 1, "language_quality": 2, "clarification": 3}
    before_cells = [cell.strip() for cell in before_row.strip("|").split("|")]
    after_cells = [cell.strip() for cell in row.strip("|").split("|")]
    for dimension in demoted:
        assert before_cells[columns[dimension]] != "not reportable by the judge"
        assert after_cells[columns[dimension]] == "not reportable by the judge"
    for column_name, index in columns.items():
        if column_name not in demoted:
            assert after_cells[index] == before_cells[index]
    assert "**Withheld judge means.**" in section
    for dimension in demoted:
        assert f"{dimension}: Rater 1" in section


def test_a_kept_dimension_keeps_its_judge_mean() -> None:
    judged = _judged_report_and_inputs()
    markdown, agreement, detail, human_means = (
        judged.markdown,
        judged.agreement,
        judged.detail,
        judged.human_means,
    )
    kept = [replace(entry, demoted=False) for entry in agreement]

    patched = apply_real_judge_validation(markdown, tuple(kept), detail, human_means=human_means)

    section = patched.split("## 4.")[1].split("## 5.")[0]
    assert "not reportable by the judge" not in section
    assert "Withheld judge means" not in section


def test_a_report_with_no_judged_system_is_patched_without_a_withheld_note() -> None:
    judged = _judged_report_and_inputs()
    agreement, detail, human_means = judged.agreement, judged.detail, judged.human_means

    patched = apply_real_judge_validation(
        render_markdown(_minimal_report()), agreement, detail, human_means=human_means
    )

    section = patched.split("## 4.")[1].split("## 5.")[0]
    assert "No system in this report was scored by the live judge." in section
    assert "Withheld judge means" not in patched


def test_withholding_needs_a_human_mean_for_every_demoted_dimension() -> None:
    judged = _judged_report_and_inputs()
    markdown, agreement, human_means = judged.markdown, judged.agreement, judged.human_means
    body = markdown.split("## 4. ")[1].split("\n\n", 1)[1].split("\n\n## 5.")[0]

    with pytest.raises(ValueError, match="no column or human mean"):
        withhold_demoted_judge_means(body, agreement, human_means[:1])


def test_regenerate_report_refuses_a_report_with_no_judge_scored_section_before_any_judge_call(
    tmp_path: Path,
) -> None:
    rater1, rater2, report = _stage(tmp_path)
    without = render_markdown(_minimal_report()).replace("Judge-scored quality", "Quality")
    report.write_text(without, encoding="utf-8")
    llm = FakeLlm(responses=_JUDGE_RESPONSES)

    with pytest.raises(ValueError, match="Judge-scored quality"):
        regenerate_report(rater1, rater2, report, LlmJudge(llm, model=_MODEL))

    assert llm.requests == []
    assert report.read_text(encoding="utf-8") == without
    assert not (tmp_path / "judge-validation-cases.csv").exists()


def test_withholding_refuses_a_quality_table_missing_a_demoted_dimensions_column() -> None:
    judged = _judged_report_and_inputs()
    body = judged.markdown.split("## 4. ")[1].split("\n\n", 1)[1].split("\n\n## 5.")[0]
    without_column = body.replace("Language quality (mean, 0-2)", "Language quality")
    assert any(
        entry.dimension == "language_quality" and entry.demoted for entry in judged.agreement
    )

    with pytest.raises(ValueError, match="no column or human mean"):
        withhold_demoted_judge_means(without_column, judged.agreement, judged.human_means)
