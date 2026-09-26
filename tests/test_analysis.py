"""
Workflow Analysis Tests
=======================

Component: ``pipelines.analysis``. Hermetic: the report is rendered from the marts of a small
service dataset whose figures are derived by hand (see ``tests.data_fixture``), and the
assumptions file is validated with hostile variants.
"""

from __future__ import annotations

# Standard libraries
import logging  # Capture log records
import shutil  # Copy the assumptions file for edits
from pathlib import Path  # Temporary locations
from typing import Any  # Mart rows

# Third-party libraries
import pytest  # Test runner and fixtures

# Local modules
from pipelines.analysis import (
    DEFAULT_ASSUMPTIONS,
    Assumptions,
    load_assumptions,
    main,
    render_workflow_analysis,
)
from pipelines.gold import MART_NAMES, build_marts, read_mart
from pipelines.silver import run_silver
from tests.data_fixture import build_service_dataset


@pytest.fixture
def silver(tmp_path: Path) -> Path:
    """A cleaned layer built from the service dataset."""
    raw = tmp_path / "raw"
    build_service_dataset(raw)
    out = tmp_path / "silver"
    run_silver(raw, out, code_version="test")
    return out


@pytest.fixture
def report(tmp_path: Path, silver: Path) -> str:
    """The report rendered from the service dataset with the shipped assumptions."""
    gold = tmp_path / "gold"
    manifest = build_marts(silver, gold, code_version="test")
    marts = {name: read_mart(gold, name) for name in MART_NAMES}
    return render_workflow_analysis(marts, load_assumptions(), manifest.as_dict())


def _edited(tmp_path: Path, old: str, new: str) -> Path:
    """A copy of the shipped assumptions with ``old`` replaced by ``new``."""
    copy = tmp_path / "assumptions.toml"
    shutil.copy(DEFAULT_ASSUMPTIONS, copy)
    text = copy.read_text(encoding="utf-8")
    assert old in text
    copy.write_text(text.replace(old, new, 1), encoding="utf-8")
    return copy


# -----------------------------------------------------------------------------
# Assumptions
# -----------------------------------------------------------------------------


def test_the_shipped_assumptions_load_and_are_ordered() -> None:
    """Low, base and high values are positive and in order; the targets are rates."""
    assumptions = load_assumptions()

    assert isinstance(assumptions, Assumptions)
    assert assumptions.agent_hour_usd.low <= assumptions.agent_hour_usd.base
    assert assumptions.handling_reason_category == "Transaccional"
    assert 0 <= assumptions.targets.sla_breach_rate_max <= 1


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "{ low = 6.0, base = 9.0, high = 14.0 }",
            "{ low = 9.0, base = 6.0, high = 14.0 }",
            "agent_hour_usd",
        ),
        (
            "{ low = 1.0, base = 1.5, high = 2.5 }",
            "{ low = 0.0, base = 1.5, high = 2.5 }",
            "contacts_per_dispute",
        ),
        ("sla_breach_rate_max = 0.05", "sla_breach_rate_max = 1.5", "between 0 and 1"),
        ("median_days_to_resolution_max = 3", "median_days_to_resolution_max = -1", "negative"),
    ],
    ids=["unordered-range", "zero-low", "rate-above-one", "negative-days"],
)
def test_invalid_assumptions_are_rejected_with_the_name_of_the_problem(
    tmp_path: Path, old: str, new: str, message: str
) -> None:
    """A bad assumption stops the analysis instead of producing plausible-looking numbers."""
    with pytest.raises(ValueError, match=message):
        load_assumptions(_edited(tmp_path, old, new))


def test_a_missing_assumption_is_named(tmp_path: Path) -> None:
    """Dropping a key reports which one is missing."""
    with pytest.raises(ValueError, match="unsafe_action_rate_max"):
        load_assumptions(_edited(tmp_path, "unsafe_action_rate_max = 0.0", ""))


# -----------------------------------------------------------------------------
# Report
# -----------------------------------------------------------------------------


def test_demand_figures_match_the_fixture(report: str) -> None:
    """Six of eight complaints are disputes, in two months."""
    assert "Dispute cases: **6** between 2025-01 and 2025-02 (2 months with cases)" in report
    assert "3 per month on average; the busiest month is 2025-02 with 3." in report
    assert "- Share of all complaints: **75.0 %** (6 of 8)." in report
    assert "- Share of `Transactions` complaints: **85.7 %**." in report
    assert "| 2025 | 6 |" in report
    assert "| Transaccional | 2 | 66.7 % |" in report


def test_resolution_figures_match_the_fixture(report: str) -> None:
    """Median 20 and P90 28 days over resolved and closed cases; one of six breached its SLA."""
    assert "median **20.0**, 90th percentile 28.0" in report
    assert "- SLA breached: **16.7 %** of cases." in report
    assert "- Repeat complainers: **16.7 %** of cases." in report
    assert "- First response recorded: 83.3 % of cases." in report
    assert "| Resolved | 2 | 33.3 % | 15.0 | 15.0 | 19.0 | 50.0 % |" in report
    assert (
        "- Cases that reached `Resolved` or `Closed`: **50.0 %**; still open, in process or "
        "escalated: 33.3 %; rejected: 16.7 %." in report
    )
    assert "| USD | 1 | 1 | 100.00 |" in report
    assert "| unknown | 3 | 0 | n/a |" in report


def test_contact_figures_match_the_fixture(report: str) -> None:
    """Transactional contacts: 7.5 min handling, 1.5 min wait, half negative, half neutral."""
    assert (
        "| Transaccional | 7.5 | 1.5 | 50.0 % | 50.0 % | -0.25 | 4.00 | 50.0 % | 0.0 % | 50.0 % |"
        in report
    )


def test_cost_is_contacts_times_hours_times_rate_at_each_assumption(report: str) -> None:
    """Handling time is 7.5 minutes (450 s mean): 0.125 h * rate * contacts."""
    assert "**7.5 minutes** (measured)" in report
    assert "| Cost per dispute | USD 0.75 | USD 1.69 | USD 4.38 |" in report
    assert "| Cost per month at 3 cases |" in report


def test_targets_are_shown_against_the_measured_baseline(report: str) -> None:
    """Baselines come from the data; targets from the assumptions file."""
    assert "| SLA breach rate | 16.7 % | <= 5.0 % |" in report
    assert "| Median days to resolution | 20.0 | <= 3 |" in report
    assert "| Safe automated resolution rate | not measured today | >= 40.0 % |" in report


def test_lineage_lists_every_input_and_mart_with_digests(report: str) -> None:
    """The last section ties the figures to the cleaned tables and the marts."""
    for name in MART_NAMES:
        assert f"`{name}`" in report
    assert "| complaints |" in report


def test_the_report_holds_no_identifier_or_free_text(report: str) -> None:
    """Only aggregates: none of the fixture's identifiers can appear."""
    for identifier in ("K1", "I1", "C1", "A1", "S1"):
        assert f"| {identifier} " not in report


def test_rendering_is_a_pure_function_of_its_inputs(tmp_path: Path, silver: Path) -> None:
    """The same marts and assumptions render the same text."""
    gold = tmp_path / "gold"
    manifest = build_marts(silver, gold, code_version="test").as_dict()
    marts = {name: read_mart(gold, name) for name in MART_NAMES}
    assumptions = load_assumptions()

    assert render_workflow_analysis(marts, assumptions, manifest) == render_workflow_analysis(
        marts, assumptions, manifest
    )


def test_empty_marts_render_without_dividing_by_zero() -> None:
    """With no data every ratio is ``n/a`` and the report still renders."""
    marts: dict[str, list[dict[str, Any]]] = {name: [] for name in MART_NAMES}
    marts["dispute_resolution_overall"] = [
        {"cases_with_days": 0, "median_days": None, "p90_days": None}
    ]
    manifest: dict[str, Any] = {"inputs": {}, "marts": {}}

    text = render_workflow_analysis(marts, load_assumptions(), manifest)

    assert "Dispute cases: **0** between n/a and n/a" in text
    assert "median **n/a**" in text
    assert "| Cost per dispute | n/a | n/a | n/a |" in text


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------


def _arguments(tmp_path: Path, silver: Path) -> list[str]:
    return [
        "--silver",
        str(silver),
        "--gold",
        str(tmp_path / "gold"),
        "--report",
        str(tmp_path / "reports" / "analysis.md"),
        "--code-version",
        "test",
    ]


def test_the_command_writes_the_report_and_a_rerun_changes_nothing(
    tmp_path: Path, silver: Path
) -> None:
    """End to end: cleaned layer in, marts and report out, byte-stable on a second run."""
    arguments = _arguments(tmp_path, silver)

    assert main(arguments) == 0
    first = (tmp_path / "reports" / "analysis.md").read_bytes()
    assert main(arguments) == 0

    assert (tmp_path / "reports" / "analysis.md").read_bytes() == first
    assert b"# Workflow analysis" in first


def test_the_command_fails_with_one_when_the_cleaned_layer_is_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No cleaned layer: exit 1, a logged reason and no report."""
    with caplog.at_level(logging.ERROR):
        code = main(_arguments(tmp_path, tmp_path / "missing"))

    assert code == 1
    assert "analysis_failed" in caplog.text
    assert not (tmp_path / "reports").exists()


def test_the_command_fails_with_one_on_invalid_assumptions(tmp_path: Path, silver: Path) -> None:
    """A bad assumptions file stops the run before any mart is built."""
    bad = _edited(tmp_path, "sla_breach_rate_max = 0.05", "sla_breach_rate_max = 2")

    code = main([*_arguments(tmp_path, silver), "--assumptions", str(bad)])

    assert code == 1
    assert not (tmp_path / "gold").exists()
