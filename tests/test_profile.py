"""
Raw Data Profiler Tests
=======================

Component: ``pipelines.profile``. Hermetic: runs DuckDB over the small dataset written by
``tests.data_fixture`` into a temporary directory, so every expected number below follows from a
defect that the fixture plants on purpose. Out of scope: the Markdown presentation (see
``test_profile_report.py``) and performance on the full data.
"""

from __future__ import annotations

# Standard libraries
import json  # Machine-readable output of the command line
import logging  # Capture progress and warning events
from pathlib import Path  # Temporary dataset locations

# Third-party libraries
import pytest  # Test runner and fixtures

# Local modules
from pipelines import profile as profiler
from pipelines.profile import ProfileError, main, profile_data
from pipelines.profile_models import DataProfile, TableProfile
from tests.data_fixture import build_dispute_dataset, write_csv


@pytest.fixture(scope="module")
def profile(tmp_path_factory: pytest.TempPathFactory) -> DataProfile:
    """Profile of the reference dataset, computed once for the module."""
    data_dir = tmp_path_factory.mktemp("raw")
    build_dispute_dataset(data_dir)
    return profile_data(data_dir)


def _table(profile: DataProfile, name: str) -> TableProfile:
    return next(t for t in profile.tables if t.name == name)


def test_only_tables_with_files_are_profiled(profile: DataProfile) -> None:
    """Tables absent from the data directory are skipped rather than reported as empty."""
    names = {t.name for t in profile.tables}

    assert names == {
        "branches",
        "customers",
        "products",
        "daily_exchange_rates",
        "transactions",
        "complaints",
        "call_center_interactions",
        "call_transcripts",
    }


def test_transaction_keys_separate_identical_redelivery_from_conflicting_versions(
    profile: DataProfile,
) -> None:
    """A repeated key is identical when only process_date differs, conflicting otherwise."""
    key = _table(profile, "transactions").key

    assert (key.rows, key.distinct_keys, key.extra_rows) == (6, 4, 2)
    assert (key.duplicate_groups, key.identical_groups, key.conflicting_groups) == (2, 1, 1)
    assert key.duplicate_rate == pytest.approx(2 / 6)


def test_orphan_references_are_counted_per_foreign_key(profile: DataProfile) -> None:
    """Only non-null references are checked, and each broken one is counted once."""
    references = {
        (fk.column, fk.ref_table): fk for fk in _table(profile, "transactions").foreign_keys
    }

    assert (
        references[("customer_id", "customers")].checked,
        references[("customer_id", "customers")].orphans,
    ) == (6, 1)
    assert (
        references[("product_id", "products")].checked,
        references[("product_id", "products")].orphans,
    ) == (6, 1)
    assert references[("branch_id", "branches")].checked == 0
    assert references[("branch_id", "branches")].orphan_rate == 0.0


def test_dimension_reference_to_a_missing_key_is_an_orphan(profile: DataProfile) -> None:
    """The customer whose branch does not exist is an orphan of registration_branch_id."""
    (reference,) = _table(profile, "customers").foreign_keys

    assert (reference.checked, reference.orphans) == (2, 1)


def test_schema_drift_is_reported_as_extra_column_and_second_header(profile: DataProfile) -> None:
    """A partition with an additional column is kept, flagged and counted as a header variant."""
    transactions = _table(profile, "transactions")

    assert transactions.extra_columns == ("new_field",)
    assert transactions.missing_columns == ()
    assert len(transactions.inventory.header_variants) == 2
    assert transactions.inventory.files_matching_declared_header == 1


def test_lateness_measures_the_gap_between_partition_and_event_day(profile: DataProfile) -> None:
    """The re-delivered rows arrive two days after the day they describe; none earlier."""
    lateness = _table(profile, "transactions").lateness

    assert lateness is not None
    assert lateness.rows_measured == 6
    assert (lateness.lag_min, lateness.lag_max) == (0, 2)
    assert lateness.lag_p50 == 0.0
    assert lateness.stamped_after_partition == 0
    assert lateness.lagged_over_7_days == 0
    assert lateness.partition_process_date_mismatches == 0


def test_event_stamped_after_its_partition_gets_negative_lag(profile: DataProfile) -> None:
    """A complaint created the day after its partition day is counted as stamped after it."""
    lateness = _table(profile, "complaints").lateness

    assert lateness is not None
    assert (lateness.rows_measured, lateness.lag_min, lateness.stamped_after_partition) == (
        2,
        -1,
        1,
    )


def test_fact_table_without_an_event_column_only_checks_partition_consistency(
    profile: DataProfile,
) -> None:
    """Transcripts carry no event timestamp, so no lag statistics are produced."""
    lateness = _table(profile, "call_transcripts").lateness

    assert lateness is not None
    assert lateness.rows_measured == 1
    assert lateness.lag_p50 is None
    assert _table(profile, "customers").lateness is None


def test_column_profile_flags_not_null_violations_decimals_and_damaged_text(
    profile: DataProfile,
) -> None:
    """Contract-relevant problems are attributed to the column that has them."""
    columns = {c.name: c for c in _table(profile, "customers").columns}

    assert columns["document_number"].nulls == 1
    assert columns["document_number"].violates_declared_not_null
    assert columns["document_number"].null_rate == pytest.approx(0.5)
    assert columns["credit_score"].integers_written_as_decimals == 1
    assert columns["address"].text_encoding_suspects == 1
    assert columns["first_name"].text_encoding_suspects == 0


def test_low_cardinality_columns_list_exact_values(profile: DataProfile) -> None:
    """Categorical columns expose their values with counts; identifiers do not."""
    columns = {c.name: c for c in _table(profile, "transactions").columns}

    assert [(v.value, v.count) for v in columns["is_fraud"].top_values] == [
        ("False", 4),
        ("True", 2),
    ]
    assert columns["transaction_id"].top_values == ()


def test_unparseable_values_are_counted_per_declared_type(tmp_path: Path) -> None:
    """A date and a boolean that do not parse are counted, and the load does not fail."""
    write_csv(
        tmp_path / "branches.csv",
        ["branch_id", "branch_opening_date", "has_atms"],
        [
            {"branch_id": "B1", "branch_opening_date": "2020-02-30", "has_atms": "maybe"},
            {"branch_id": "B2", "branch_opening_date": "2020-02-28", "has_atms": "True"},
        ],
    )

    columns = {c.name: c for c in _table(profile_data(tmp_path), "branches").columns}

    assert columns["branch_opening_date"].unparseable == 1
    assert columns["has_atms"].unparseable == 1


def test_domain_facts_summarise_fraud_usd_contacts_and_complaints(profile: DataProfile) -> None:
    """The workload facts are computed from the same rows as the table profiles."""
    facts = profile.facts
    assert facts is not None

    assert (facts.fraud.transactions, facts.fraud.positives) == (6, 2)
    assert [(m.month, m.rows, m.positives) for m in facts.fraud.by_month] == [("2025-01", 6, 2)]
    assert facts.fraud.prevalence == pytest.approx(1 / 3)
    assert (facts.usd_amounts.present, facts.usd_amounts.within_tolerance) == (5, 4)
    assert (facts.usd_amounts.outside_tolerance, facts.usd_amounts.without_rate) == (1, 0)
    assert (facts.contacts.interactions, facts.contacts.flagged_with_transcript) == (2, 1)
    assert (facts.contacts.transcripts, facts.contacts.transcript_interactions) == (1, 1)
    assert [(v.value, v.count) for v in facts.complaints.categories] == [("Fees", 1), ("Fraud", 1)]
    assert (facts.complaints.complaints, facts.complaints.repeat_complainers) == (2, 1)
    assert facts.complaints.sla_breached == 1


def test_usd_check_without_a_rates_table_reports_everything_as_uncheckable(
    tmp_path: Path,
) -> None:
    """With no exchange-rate file nothing can be verified, and nothing is called consistent."""
    write_csv(
        tmp_path
        / "transactions"
        / "year=2025"
        / "month=01"
        / "day=10"
        / "transactions_20250110.csv",
        ["transaction_id", "transaction_date", "process_date", "amount", "amount_usd", "currency"],
        [
            {
                "transaction_id": "T1",
                "transaction_date": "2025-01-10 08:00:00",
                "process_date": "2025-01-10",
                "amount": "10",
                "amount_usd": "2",
                "currency": "MXN",
            }
        ],
    )

    facts = profile_data(tmp_path).facts

    assert facts is not None
    assert (facts.usd_amounts.present, facts.usd_amounts.without_rate) == (1, 1)
    assert facts.usd_amounts.within_tolerance == 0


def test_missing_tables_are_logged_and_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An empty data directory yields an empty profile with one warning per table."""
    with caplog.at_level(logging.WARNING, logger="pipelines.profile"):
        profile = profile_data(tmp_path)

    assert profile.tables == ()
    assert sum("profile_table_missing" in record.message for record in caplog.records) == 13


def test_table_whose_header_lacks_the_key_is_reported_as_unloadable(tmp_path: Path) -> None:
    """A file without the primary key in its header cannot be profiled, and the table is named."""
    write_csv(tmp_path / "branches.csv", ["branch_code"], [{"branch_code": "S1"}])

    profile = profile_data(tmp_path)

    assert profile.tables == ()
    assert [(t.name, t.reason.split(":")[0]) for t in profile.unloadable_tables] == [
        ("branches", "InvalidHeader")
    ]


def test_loaded_table_that_lacks_its_key_column_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Should the parsed columns ever lack the key, profiling stops and names the table."""
    write_csv(tmp_path / "branches.csv", ["branch_id"], [{"branch_id": "B1"}])
    monkeypatch.setattr(profiler, "_verify_headers", lambda *_args: None)
    monkeypatch.setattr(profiler, "_load_table", lambda *_args: {"other"})

    with pytest.raises(ProfileError, match="branches lacks key column"):
        profile_data(tmp_path)


def test_command_line_writes_markdown_and_json_reports(tmp_path: Path) -> None:
    """The entry point writes both artefacts and exits 0."""
    data_dir = tmp_path / "raw"
    build_dispute_dataset(data_dir)
    report, json_path = tmp_path / "out" / "profile.md", tmp_path / "out" / "profile.json"

    exit_code = main(
        ["--data-dir", str(data_dir), "--report", str(report), "--json", str(json_path)]
    )

    assert exit_code == 0
    assert report.read_text(encoding="utf-8").startswith("# Raw Data Profile")
    document = json.loads(json_path.read_text(encoding="utf-8"))
    assert {table["name"] for table in document["tables"]} >= {"transactions", "customers"}


def test_command_line_reports_failure_with_exit_code_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A profiling error is logged and turned into a non-zero exit code, not a traceback."""
    write_csv(tmp_path / "branches.csv", ["branch_id"], [{"branch_id": "B1"}])
    monkeypatch.setattr(profiler, "_verify_headers", lambda *_args: None)
    monkeypatch.setattr(profiler, "_load_table", lambda *_args: {"other"})

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "--report",
            str(tmp_path / "r.md"),
            "--json",
            str(tmp_path / "r.json"),
        ]
    )

    assert exit_code == 1
    assert not (tmp_path / "r.md").exists()


def test_profile_is_deterministic(tmp_path: Path) -> None:
    """Profiling the same data twice yields identical results, digest included."""
    build_dispute_dataset(tmp_path)

    assert profile_data(tmp_path) == profile_data(tmp_path)
