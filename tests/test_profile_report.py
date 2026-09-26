"""
Profile Report Tests
====================

Component: ``pipelines.profile_report``. Hermetic: the renderer is a pure function, exercised on
the profile of the reference dataset and on hand-built profiles for the boundary cases.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import replace  # Derive variations of a profile
from pathlib import Path  # Temporary dataset location

# Third-party libraries
import pytest  # Test runner and fixtures

# Local modules
from pipelines.profile import profile_data
from pipelines.profile_models import DataProfile, KeyProfile
from pipelines.profile_report import Assumption, Verdict, assess_assumptions, render_markdown
from tests.data_fixture import build_dispute_dataset


@pytest.fixture(scope="module")
def profile(tmp_path_factory: pytest.TempPathFactory) -> DataProfile:
    """Profile of the reference dataset."""
    data_dir = tmp_path_factory.mktemp("raw")
    build_dispute_dataset(data_dir)
    return profile_data(data_dir)


def _verdicts(profile: DataProfile) -> dict[str, Verdict]:
    return {a.statement: a.verdict for a in assess_assumptions(profile)}


def test_report_has_every_section_and_the_snapshot_digest(profile: DataProfile) -> None:
    """The report is complete and names the snapshot it describes."""
    text = render_markdown(profile)

    for heading in (
        "# Raw Data Profile",
        "## 1. Assumptions checked against the data",
        "## 2. Inventory",
        "## 3. Files and schema",
        "## 4. Keys and duplicates",
        "## 5. Missing and malformed values",
        "## 6. Referential integrity",
        "## 7. Arrival lateness",
        "## 8. Workload facts",
        "## Appendix. Column detail",
    ):
        assert heading in text
    assert profile.inventory_digest in text
    assert text.endswith("\n")


def test_rendering_is_a_pure_function_of_the_profile(profile: DataProfile) -> None:
    """The same profile always renders to identical text."""
    assert render_markdown(profile) == render_markdown(profile)


def test_figures_in_the_text_come_from_the_profile(profile: DataProfile) -> None:
    """Spot-check that counts and rates appear as formatted from the measured values."""
    text = render_markdown(profile)

    assert "| transactions | fact | 2 |" in text
    assert "2025-01-10 → 2025-01-12" in text
    assert "33.33 %" in text  # duplicate rate of transactions: 2 of 6 rows
    assert "registration_branch_id → branches.branch_id | 2 | 1 | 50.00 %" in text
    assert "`Fees` (1), `Fraud` (1)" in text


def test_verdicts_follow_the_measurements(profile: DataProfile) -> None:
    """Each rule reaches the verdict the planted defects imply."""
    verdicts = _verdicts(profile)

    assert (
        verdicts["Fact tables are partitioned as year/month/day, one file per day"] is Verdict.HOLDS
    )
    assert (
        verdicts["Files are UTF-8 CSV whose header equals the dictionary's column list"]
        is Verdict.DIFFERS
    )
    assert verdicts["Row counts match the dictionary"] is Verdict.DIFFERS
    assert verdicts["Only a small share of foreign keys are orphans"] is Verdict.DIFFERS
    assert verdicts["Partitions can arrive after the day they describe"] is Verdict.HOLDS
    assert verdicts["Schemas may evolve between partitions"] is Verdict.INFORMATIONAL
    usd = "amount_usd is present and consistent with the daily exchange rate"
    assert verdicts[usd] is Verdict.DIFFERS


def test_clean_data_earns_holds_verdicts(tmp_path: Path) -> None:
    """A dataset with no defects satisfies the layout, header and orphan assumptions."""
    from tests.data_fixture import write_dimension  # noqa: PLC0415 - local, only this test needs it

    write_dimension(tmp_path, "branches", [{"branch_id": "B1", "branch_code": "S1"}])

    verdicts = _verdicts(profile_data(tmp_path))

    assert (
        verdicts["Files are UTF-8 CSV whose header equals the dictionary's column list"]
        is Verdict.HOLDS
    )
    assert (
        verdicts["Fact tables are partitioned as year/month/day, one file per day"] is Verdict.HOLDS
    )


def test_assumptions_without_workload_facts_stop_before_the_workload_rules(
    profile: DataProfile,
) -> None:
    """When no workload facts were measured, only the layout and quality rules are reported."""
    with_facts = assess_assumptions(profile)
    without_facts = assess_assumptions(replace(profile, facts=None))

    assert len(without_facts) == len(with_facts) - 4
    assert "No workload facts were measured." in render_markdown(replace(profile, facts=None))


@pytest.mark.parametrize(
    ("extra_rows", "expected"),
    [(0, Verdict.DIFFERS), (2, Verdict.HOLDS), (30, Verdict.DIFFERS)],
    ids=["none-observed", "inside-the-band", "far-above-the-band"],
)
def test_duplicate_assumption_uses_a_band_around_two_percent(
    profile: DataProfile, extra_rows: int, expected: Verdict
) -> None:
    """Around 2 % duplicates holds; none, or far more, does not."""
    tables = tuple(
        replace(t, key=KeyProfile(100, 100 - extra_rows, 0, 0, 0)) if t.name == "customers" else t
        for t in profile.tables
        if t.name == "customers"
    )

    verdict = _verdicts(replace(profile, tables=tables))["About 2 % of records are duplicates"]

    assert verdict is expected


def test_assumption_is_a_plain_value_object() -> None:
    """Assumptions compare by value, so tests and reports can rely on equality."""
    first = Assumption("s", "e", "o", Verdict.HOLDS)

    assert first == Assumption("s", "e", "o", Verdict.HOLDS)
    assert first != Assumption("s", "e", "o", Verdict.DIFFERS)
