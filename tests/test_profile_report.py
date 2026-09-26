"""
Profile Report Tests
====================

Component: ``pipelines.profile_report``. Hermetic: the renderer is a pure function, exercised on
the profile of the reference dataset, on the profile of an empty directory and on hand-built
profiles for the boundary cases of each verdict rule.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Callable  # Type of the parametrized profile changes
from dataclasses import replace  # Derive variations of a profile
from pathlib import Path  # Temporary dataset locations

# Third-party libraries
import pytest  # Test runner and fixtures

# Local modules
from pipelines.inventory import HeaderVariant, TableInventory
from pipelines.profile import profile_data
from pipelines.profile_models import (
    ColumnProfile,
    ComplaintFacts,
    ContactFacts,
    DataProfile,
    DomainFacts,
    ForeignKeyProfile,
    FraudFacts,
    KeyProfile,
    LatenessProfile,
    TableProfile,
    UsdAmountFacts,
)
from pipelines.profile_report import Verdict, assess_assumptions, render_markdown
from pipelines.sources import table
from tests.data_fixture import build_dispute_dataset

LAYOUT = "Fact tables are partitioned as year/month/day, one file per day"
HEADERS = "Files are UTF-8 CSV whose header equals the dictionary's column list"
ROW_COUNTS = "Row counts match the dictionary"
DUPLICATES = "About 2 % of records are duplicates"
NULLS = "About 5 % of values are missing in nullable fields"
ORPHANS = "Only a small share of foreign keys are orphans"
LATENESS = "Partitions can arrive after the day they describe"
USD = "amount_usd is present and consistent with the daily exchange rate"
THRESHOLD_RULES = (LAYOUT, HEADERS, ROW_COUNTS, DUPLICATES, NULLS, ORPHANS, LATENESS, USD)


@pytest.fixture(scope="module")
def profile(tmp_path_factory: pytest.TempPathFactory) -> DataProfile:
    """Profile of the reference dataset."""
    data_dir = tmp_path_factory.mktemp("raw")
    build_dispute_dataset(data_dir)
    return profile_data(data_dir)


def _verdicts(profile: DataProfile) -> dict[str, Verdict]:
    return {a.statement: a.verdict for a in assess_assumptions(profile)}


def _healthy_profile() -> DataProfile:
    """A hand-built profile in which every assumption is satisfied, with round numbers."""
    spec = table("customers")
    inventory = TableInventory(
        name="customers",
        files=1,
        total_bytes=10,
        bom_files=1,
        undecodable_headers=0,
        partition_days=0,
        first_partition=None,
        last_partition=None,
        missing_partition_days=0,
        nonconforming_paths=0,
        header_variants=(HeaderVariant(spec.column_names, 1),),
        files_matching_declared_header=1,
    )
    nullable = ColumnProfile("email", "VARCHAR(100)", True, 100, 5, 0, 0, 0, 90, ())
    reference = ForeignKeyProfile("registration_branch_id", "branches", "branch_id", 100, 0)
    customers = TableProfile(
        name="customers",
        inventory=inventory,
        expected_rows=100,
        key=KeyProfile(100, 98, 0, 2, 2, 0),
        extra_columns=(),
        missing_columns=(),
        columns=(nullable,),
        foreign_keys=(reference,),
        skipped_references=(),
        lateness=LatenessProfile(100, 0, 0, 0.0, 1.0, 2.0, 2, 0, 0, 0, None, None),
    )
    facts = DomainFacts(
        fraud=FraudFacts(100, 1, ()),
        usd_amounts=UsdAmountFacts(100, 100, 0, 0),
        contacts=ContactFacts(10, 5, 5, 5, (), ()),
        complaints=ComplaintFacts(10, 1, 1, ()),
    )
    return DataProfile("digest", (customers,), facts)


def test_report_has_every_section_and_the_snapshot_digest(profile: DataProfile) -> None:
    """The report is complete and names the snapshot it describes."""
    text = render_markdown(profile)

    for heading in (
        "# Raw Data Profile",
        "## 1. Assumptions checked against the data",
        "## 2. Tables not profiled",
        "## 3. Inventory",
        "## 4. Files and schema",
        "## 5. Keys and duplicates",
        "## 6. Missing and malformed values",
        "## 7. Referential integrity",
        "## 8. Arrival lateness",
        "## 9. Workload facts",
        "## Appendix. Column detail",
    ):
        assert heading in text
    assert profile.inventory_digest in text
    assert text.endswith("\n")


def test_two_independent_profiles_of_the_same_data_render_identically(tmp_path: Path) -> None:
    """The same data always yields byte-identical text, whichever directory it sits in."""
    first, second = tmp_path / "first", tmp_path / "second"
    build_dispute_dataset(first)
    build_dispute_dataset(second)

    assert render_markdown(profile_data(first)) == render_markdown(profile_data(second))


def test_figures_in_the_text_come_from_the_profile(profile: DataProfile) -> None:
    """Spot-check that counts and rates appear as formatted from the measured values."""
    text = render_markdown(profile)

    assert "| transactions | fact | 2 |" in text
    assert "2025-01-10 → 2025-01-12" in text
    assert "33.33 %" in text  # duplicate rate of transactions: 2 of 6 rows
    assert "registration_branch_id → branches.branch_id | 2 | 1 | 50.00 %" in text
    assert "`Fees` (1), `Fraud` (1)" in text
    assert "stamped after their partition day (event hours 10 to 10)" in text


def test_verdicts_follow_the_measurements(profile: DataProfile) -> None:
    """Each rule reaches the verdict the planted defects imply."""
    verdicts = _verdicts(profile)

    assert verdicts[LAYOUT] is Verdict.HOLDS
    assert verdicts[HEADERS] is Verdict.DIFFERS
    assert verdicts[ROW_COUNTS] is Verdict.DIFFERS
    assert verdicts[ORPHANS] is Verdict.DIFFERS
    assert verdicts[LATENESS] is Verdict.HOLDS
    assert verdicts["Schemas may evolve between partitions"] is Verdict.INFORMATIONAL
    assert verdicts[USD] is Verdict.DIFFERS


def test_a_fully_satisfied_profile_holds_every_rule() -> None:
    """The positive branch of every threshold rule is reachable and reads Holds."""
    verdicts = _verdicts(_healthy_profile())

    for statement in THRESHOLD_RULES:
        assert verdicts[statement] is Verdict.HOLDS, statement


def _more_rows_expected(t: TableProfile) -> TableProfile:
    return replace(t, expected_rows=200)


def _no_duplicates(t: TableProfile) -> TableProfile:
    return replace(t, key=KeyProfile(100, 100, 0, 0, 0, 0))


def _many_duplicates(t: TableProfile) -> TableProfile:
    return replace(t, key=KeyProfile(100, 60, 0, 40, 40, 0))


def _many_nulls(t: TableProfile) -> TableProfile:
    return replace(t, columns=(replace(t.columns[0], nulls=40),))


def _many_orphans(t: TableProfile) -> TableProfile:
    return replace(t, foreign_keys=(replace(t.foreign_keys[0], orphans=50),))


def _no_lag(t: TableProfile) -> TableProfile:
    assert t.lateness is not None
    return replace(t, lateness=replace(t.lateness, lag_max=0))


@pytest.mark.parametrize(
    ("change", "statement"),
    [
        (_more_rows_expected, ROW_COUNTS),
        (_no_duplicates, DUPLICATES),
        (_many_duplicates, DUPLICATES),
        (_many_nulls, NULLS),
        (_many_orphans, ORPHANS),
        (_no_lag, LATENESS),
    ],
    ids=["row-count", "no-duplicates", "too-many-duplicates", "nulls", "orphans", "no-lag"],
)
def test_each_rule_reports_differs_when_its_measurement_leaves_the_band(
    change: Callable[[TableProfile], TableProfile], statement: str
) -> None:
    """Moving one measurement out of its band flips exactly that verdict to Differs."""
    healthy = _healthy_profile()
    altered = replace(healthy, tables=(change(healthy.tables[0]),))

    assert _verdicts(altered)[statement] is Verdict.DIFFERS


def test_one_broken_reference_is_not_hidden_by_a_small_overall_share() -> None:
    """Orphans are judged per reference: 50 % on one reference fails although the total is 0.5 %."""
    healthy = _healthy_profile()
    customers = healthy.tables[0]
    references = (
        ForeignKeyProfile("registration_branch_id", "branches", "branch_id", 10_000, 0),
        ForeignKeyProfile("segment", "branches", "branch_id", 100, 50),
    )
    altered = replace(healthy, tables=(replace(customers, foreign_keys=references),))

    observed = next(a.observed for a in assess_assumptions(altered) if a.statement == ORPHANS)

    assert _verdicts(altered)[ORPHANS] is Verdict.DIFFERS
    assert "(0.50 %)" in observed
    assert "worst customers.segment → branches (50.00 %)" in observed


def test_usd_verdict_needs_both_presence_and_consistency() -> None:
    """A field that is mostly missing, or often inconsistent, is not usable."""
    healthy = _healthy_profile()
    assert healthy.facts is not None
    mostly_missing = replace(
        healthy, facts=replace(healthy.facts, usd_amounts=UsdAmountFacts(40, 40, 0, 0))
    )
    inconsistent = replace(
        healthy, facts=replace(healthy.facts, usd_amounts=UsdAmountFacts(100, 50, 50, 0))
    )

    assert _verdicts(mostly_missing)[USD] is Verdict.DIFFERS
    assert _verdicts(inconsistent)[USD] is Verdict.DIFFERS


def test_nothing_to_check_reads_not_assessed_never_holds(tmp_path: Path) -> None:
    """With no data at all, no threshold rule may claim that an assumption holds."""
    verdicts = _verdicts(profile_data(tmp_path))

    assert Verdict.HOLDS not in verdicts.values()
    assert Verdict.DIFFERS not in verdicts.values()
    for statement in THRESHOLD_RULES:
        assert verdicts[statement] is Verdict.NOT_ASSESSED, statement


def test_absent_tables_and_skipped_references_are_listed_with_their_reason(
    profile: DataProfile, tmp_path: Path
) -> None:
    """What could not be measured is visible in the report, not only in the log."""
    text = render_markdown(profile)
    empty_text = render_markdown(profile_data(tmp_path))

    assert "| digital_events | no files found |" in text
    assert "| customers | no files found |" in empty_text
    assert "References that could not be checked:" in text
    assert "table service_agents was not profiled" in text


def test_assumptions_without_workload_facts_stop_before_the_workload_rules(
    profile: DataProfile,
) -> None:
    """When no workload facts were measured, only the layout and quality rules are reported."""
    with_facts = assess_assumptions(profile)
    without_facts = assess_assumptions(replace(profile, facts=None))

    assert len(without_facts) == len(with_facts) - 4
    assert "No workload facts were measured." in render_markdown(replace(profile, facts=None))


def test_null_verdict_reports_the_typical_column_not_only_the_overall_share(
    profile: DataProfile,
) -> None:
    """The overall share is dominated by optional columns, so the median column is shown too."""
    observed = next(a.observed for a in assess_assumptions(profile) if a.statement == NULLS)

    assert "median column" in observed
