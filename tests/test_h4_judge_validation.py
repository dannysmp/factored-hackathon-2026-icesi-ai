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
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Third-party libraries
import pytest

# Local modules
from app.llm.client import FakeLlm
from contracts.service_v1.envelope import Intent
from evals import h4_judge_validation
from evals.h4_judge_validation import (
    RaterCaseRow,
    _as_rater_scores,
    _check_roles,
    _check_same_prepared_packet,
    apply_real_judge_validation,
    load_rater_sheet,
    main,
    regenerate_report,
    score_with_judge,
)
from evals.judge import LlmJudge
from evals.judge_validation import compute_agreement
from evals.metrics import CaseResult, compute_headline_metrics
from evals.models import Case, CaseCategory
from evals.repeated_runs import compute_variability
from evals.report import EvaluationReport, SystemResult, Versions, render_markdown

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


def test_apply_real_judge_validation_replaces_section_7_and_drops_the_stale_bullet() -> None:
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

    after_text = apply_real_judge_validation(before_text, agreement)

    assert "Pending H4" not in after_text
    assert "pending the real H4 human sample" not in after_text
    assert "Judge-validation sample provenance: `human`." in after_text
    assert "yes (human-only in this report)" in after_text  # language_quality's own demotion

    # Every other section is untouched: the same "after" text, rendered directly from a report
    # that already carried the real agreement and human provenance, matches exactly.
    same_report = _minimal_report(judge_validation=agreement, judge_validation_provenance="human")
    assert after_text == render_markdown(same_report)


def test_apply_real_judge_validation_raises_on_an_unrecognized_report_shape() -> None:
    with pytest.raises(ValueError, match="cannot recognize"):
        apply_real_judge_validation("not a real report at all", agreement=())


def test_apply_real_judge_validation_refuses_section_8_missing_or_out_of_order() -> None:
    text = render_markdown(_minimal_report())
    without_8 = text.replace("## 8. Learned components", "## Learned components")
    with pytest.raises(ValueError, match="cannot recognize"):
        apply_real_judge_validation(without_8, agreement=())

    moved = text.replace("\n\n## 8. Learned components", "\n\n## 8b. Learned components")
    out_of_order = moved.replace(
        "## 7. Judge validation", "## 8. Learned components\n\nx\n\n## 7. Judge validation"
    )
    with pytest.raises(ValueError, match="cannot recognize"):
        apply_real_judge_validation(out_of_order, agreement=())


def test_apply_real_judge_validation_drops_the_bullet_without_a_trailing_newline() -> None:
    text = render_markdown(_minimal_report())
    assert text.endswith("\n")
    bullet = "- The judge-validation section is pending the real H4 human sample; see that "
    assert bullet in text
    # The bullet is the last line of the report, with no newline after it.
    head = text.split(bullet)[0]
    trimmed = head + bullet + "section for detail."
    assert "pending the real H4 human sample" not in apply_real_judge_validation(trimmed, ())


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

    expected = render_markdown(
        _minimal_report(judge_validation=agreement, judge_validation_provenance="human")
    )
    assert report.read_text(encoding="utf-8") == expected
    language_quality = {row.dimension: row for row in agreement}["language_quality"]
    assert language_quality.rater1_to_judge == pytest.approx(2 / 3)
    assert language_quality.rater2_to_judge == pytest.approx(1.0)
    assert len(llm.requests) == 3
    assert not list(tmp_path.glob(".evaluation.md.*"))  # no temporary file left behind
    assert "judge_validation_report_updated" in caplog.text
    assert "dimension=language_quality" in caplog.text
    assert "demoted=True" in caplog.text
    assert "CASE-001" not in caplog.text  # no sheet text in the logs
    assert "No reconozco" not in caplog.text


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
