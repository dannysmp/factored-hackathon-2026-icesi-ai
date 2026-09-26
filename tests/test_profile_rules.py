"""
Profiler Rule Tests
===================

Component: ``pipelines.profile``. Hermetic: each test writes the smallest dataset that isolates
one measurement rule and asserts the exact numbers, so that changing a rule (a join condition, a
NULL filter, a pattern, a threshold) changes a result and fails a test. Complements
``test_profile.py``, which checks the reference dataset end to end.
"""

from __future__ import annotations

# Standard libraries
import logging  # Capture log records to check that data values never reach them
from datetime import date  # Partition day
from pathlib import Path  # Temporary dataset locations

# Third-party libraries
import pytest  # Test runner and fixtures

# Local modules
from pipelines import profile as profiler
from pipelines.inventory import scan_table
from pipelines.profile import main, profile_data
from pipelines.profile_models import DataProfile, DomainFacts, TableProfile
from pipelines.profile_report import Verdict, assess_assumptions, render_markdown
from pipelines.sources import table
from tests.data_fixture import (
    build_dispute_dataset,
    write_csv,
    write_dimension,
    write_partition,
)

DAY = date(2025, 1, 10)
SENTINEL = "SENTINEL_VALUE_9F3A"


def _table(profile: DataProfile, name: str) -> TableProfile:
    return next(t for t in profile.tables if t.name == name)


def _facts(profile: DataProfile) -> DomainFacts:
    assert profile.facts is not None
    return profile.facts


def _transaction(key: str, currency: str, amount: str, usd: str) -> dict[str, str]:
    return {
        "transaction_id": key,
        "transaction_date": "2025-01-10 08:00:00",
        "process_date": "2025-01-10",
        "amount": amount,
        "currency": currency,
        "amount_usd": usd,
    }


# -----------------------------------------------------------------------------
# USD amounts
# -----------------------------------------------------------------------------


def test_usd_check_matches_each_amount_with_the_rate_of_its_own_currency(tmp_path: Path) -> None:
    """Three currencies on one day: each amount is compared only with its own day's rate."""
    write_dimension(
        tmp_path,
        "daily_exchange_rates",
        [
            {
                "date": "2025-01-10",
                "source_currency": "MXN",
                "target_currency": "USD",
                "exchange_rate": "0.05",
            },
            {
                "date": "2025-01-10",
                "source_currency": "COP",
                "target_currency": "USD",
                "exchange_rate": "0.00025",
            },
        ],
    )
    write_partition(
        tmp_path,
        "transactions",
        DAY,
        [
            _transaction("T1", "MXN", "100.00", "5.00"),
            _transaction("T2", "COP", "1000000.00", "250.00"),
            _transaction("T3", "USD", "7.00", "7.00"),
        ],
    )

    usd = _facts(profile_data(tmp_path)).usd_amounts

    assert (usd.present, usd.within_tolerance, usd.outside_tolerance, usd.without_rate) == (
        3,
        3,
        0,
        0,
    )


def test_usd_check_separates_deviating_amounts_from_amounts_without_a_rate(
    tmp_path: Path,
) -> None:
    """An amount far from the rate is outside tolerance; an unknown currency is uncheckable."""
    write_dimension(
        tmp_path,
        "daily_exchange_rates",
        [
            {
                "date": "2025-01-10",
                "source_currency": "MXN",
                "target_currency": "USD",
                "exchange_rate": "0.05",
            }
        ],
    )
    write_partition(
        tmp_path,
        "transactions",
        DAY,
        [
            _transaction("T1", "MXN", "100.00", "5.10"),  # 2 % away: inside the 3 % tolerance
            _transaction("T2", "MXN", "100.00", "9.00"),  # far away
            _transaction("T3", "ARS", "100.00", "1.00"),  # no ARS rate that day
        ],
    )

    usd = _facts(profile_data(tmp_path)).usd_amounts

    assert (usd.within_tolerance, usd.outside_tolerance, usd.without_rate) == (1, 1, 1)


# -----------------------------------------------------------------------------
# References, keys and text
# -----------------------------------------------------------------------------


def test_missing_values_in_the_referenced_column_do_not_hide_orphans(tmp_path: Path) -> None:
    """A NULL among the referenced keys must not turn every comparison into 'unknown'."""
    write_dimension(
        tmp_path,
        "customers",
        [{"customer_id": ""}, {"customer_id": "C1"}],
    )
    write_dimension(
        tmp_path,
        "products",
        [
            {"product_id": "P1", "customer_id": "C1"},
            {"product_id": "P2", "customer_id": "C-GHOST"},
        ],
    )

    references = _table(profile_data(tmp_path), "products").foreign_keys
    owner = next(fk for fk in references if fk.ref_table == "customers")

    assert (owner.checked, owner.orphans) == (2, 1)


def test_rows_with_a_missing_key_are_counted_apart_from_duplicates(tmp_path: Path) -> None:
    """Two rows without a key are a completeness problem, not a repeated record."""
    write_dimension(
        tmp_path,
        "customers",
        [
            {"customer_id": "", "first_name": "A"},
            {"customer_id": "", "first_name": "B"},
            {"customer_id": "C1", "first_name": "C"},
            {"customer_id": "C1", "first_name": "C"},
        ],
    )

    key = _table(profile_data(tmp_path), "customers").key

    assert (key.rows, key.null_keys, key.distinct_keys, key.extra_rows) == (4, 2, 1, 1)
    assert (key.duplicate_groups, key.identical_groups, key.conflicting_groups) == (1, 1, 0)
    assert key.duplicate_rate == pytest.approx(0.25)


def test_a_column_added_by_a_later_partition_does_not_make_a_redelivery_conflicting(
    tmp_path: Path,
) -> None:
    """Repeated keys are compared on the declared columns, so schema evolution is not conflict."""
    write_partition(tmp_path, "transactions", DAY, [_transaction("T1", "MXN", "1", "1")])
    write_partition(
        tmp_path,
        "transactions",
        date(2025, 1, 11),
        [{**_transaction("T1", "MXN", "1", "1"), "process_date": "2025-01-11", "new_field": "x"}],
        extra_columns=("new_field",),
    )

    key = _table(profile_data(tmp_path), "transactions").key

    assert (key.identical_groups, key.conflicting_groups) == (1, 0)


def test_every_supported_kind_of_text_damage_is_detected(tmp_path: Path) -> None:
    """Latin-1 misreading of accents, of punctuation and the replacement character all count."""
    write_dimension(
        tmp_path,
        "customers",
        [
            {"customer_id": "C1", "address": "Calle 5 número 3, barrio centro de la ciudad"},
            {"customer_id": "C2", "address": "Calle 9 Ã© alto en la zona centro de la ciudad"},
            {"customer_id": "C3", "address": "Precio â€™ alto en la zona centro de la ciudad"},
            {"customer_id": "C4", "address": "Texto roto � en la zona centro de la ciudad"},
        ],
    )

    address = next(
        c for c in _table(profile_data(tmp_path), "customers").columns if c.name == "address"
    )

    assert address.text_encoding_suspects == 3


@pytest.mark.parametrize(
    ("distinct_values", "listed"),
    [(25, 25), (26, 0)],
    ids=["at-the-limit", "just-above-the-limit"],
)
def test_values_are_listed_only_for_columns_with_few_distinct_values(
    tmp_path: Path, distinct_values: int, listed: int
) -> None:
    """A column with at most 25 distinct values is listed exactly; a richer one is not."""
    write_dimension(
        tmp_path,
        "branches",
        [{"branch_id": f"B{i}", "branch_type": f"Type{i}"} for i in range(distinct_values)],
    )

    column = next(
        c for c in _table(profile_data(tmp_path), "branches").columns if c.name == "branch_type"
    )

    assert len(column.top_values) == listed


# -----------------------------------------------------------------------------
# Lateness
# -----------------------------------------------------------------------------


def test_events_stamped_after_their_partition_day_report_the_hours_they_fall_in(
    tmp_path: Path,
) -> None:
    """Events at 02:00 and 05:00 of the next day point to a partition cut in another time zone."""
    write_partition(
        tmp_path,
        "transactions",
        DAY,
        [
            {**_transaction("T1", "MXN", "1", "1"), "transaction_date": "2025-01-11 02:00:00"},
            {**_transaction("T2", "MXN", "1", "1"), "transaction_date": "2025-01-11 05:00:00"},
            _transaction("T3", "MXN", "1", "1"),
        ],
    )

    lateness = _table(profile_data(tmp_path), "transactions").lateness

    assert lateness is not None
    assert lateness.stamped_after_partition == 2
    assert (lateness.stamped_after_first_hour, lateness.stamped_after_last_hour) == (2, 5)


def test_no_hours_are_reported_when_no_event_is_stamped_after_its_partition(
    tmp_path: Path,
) -> None:
    """The hour range is absent, not zero, when there is nothing to locate."""
    write_partition(tmp_path, "transactions", DAY, [_transaction("T1", "MXN", "1", "1")])

    lateness = _table(profile_data(tmp_path), "transactions").lateness

    assert lateness is not None
    assert (lateness.stamped_after_first_hour, lateness.stamped_after_last_hour) == (None, None)


# -----------------------------------------------------------------------------
# Unparseable tables never leak their content
# -----------------------------------------------------------------------------


def _write_ragged_branches(data_dir: Path) -> None:
    """A dimension file whose second row has one field more than the header."""
    path = data_dir / "branches.csv"
    path.write_text(f"branch_id,branch_code\nB1,S1\nB2,S2,{SENTINEL}\n", encoding="utf-8")


def test_table_that_cannot_be_parsed_is_reported_and_the_rest_is_still_profiled(
    tmp_path: Path,
) -> None:
    """One malformed file does not abort the run; the table and the cause are listed."""
    _write_ragged_branches(tmp_path)
    write_dimension(tmp_path, "customers", [{"customer_id": "C1", "registration_branch_id": "B1"}])

    profile = profile_data(tmp_path)

    assert [t.name for t in profile.tables] == ["customers"]
    assert [t.name for t in profile.unloadable_tables] == ["branches"]
    assert "could not be parsed" in profile.unloadable_tables[0].reason
    reasons = {r.reason for r in _table(profile, "customers").skipped_references}
    assert "table branches was not profiled" in reasons


def test_no_data_value_reaches_the_error_the_log_the_report_or_stderr(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """The offending line is never quoted: not in the reason, the log, the report or stderr."""
    _write_ragged_branches(tmp_path)
    report, json_path = tmp_path / "out.md", tmp_path / "out.json"

    with caplog.at_level(logging.DEBUG):
        exit_code = main(
            ["--data-dir", str(tmp_path), "--report", str(report), "--json", str(json_path)]
        )

    assert exit_code == 0
    assert SENTINEL not in caplog.text
    assert SENTINEL not in capsys.readouterr().err
    assert SENTINEL not in report.read_text(encoding="utf-8")
    assert SENTINEL not in json_path.read_text(encoding="utf-8")
    assert "| branches |" in report.read_text(encoding="utf-8")


def test_unloadable_tables_are_listed_in_the_report(tmp_path: Path) -> None:
    """The report says which table failed and that its files could not be parsed."""
    _write_ragged_branches(tmp_path)

    text = render_markdown(profile_data(tmp_path))

    assert "the files of table branches could not be parsed" in text


# -----------------------------------------------------------------------------
# Reference dataset details
# -----------------------------------------------------------------------------


def test_references_that_cannot_be_checked_are_returned_with_their_reason(
    tmp_path: Path,
) -> None:
    """A skipped foreign key is recorded, so nothing is silently dropped."""
    build_dispute_dataset(tmp_path)

    skipped = {
        (r.column, r.ref_table): r.reason
        for r in _table(profile_data(tmp_path), "call_transcripts").skipped_references
    }

    assert skipped == {("agent_id", "service_agents"): "table service_agents was not profiled"}


def test_reference_to_a_column_missing_from_the_referenced_table_is_reported(
    tmp_path: Path,
) -> None:
    """When the referenced table lacks the referenced column the reason names that column."""
    write_csv(tmp_path / "branches.csv", ["branch_id", "branch_code"], [{"branch_id": "B1"}])
    write_csv(
        tmp_path / "customers.csv",
        ["customer_id", "registration_branch_id"],
        [{"customer_id": "C1", "registration_branch_id": "B1"}],
    )
    write_csv(tmp_path / "products.csv", ["product_id"], [{"product_id": "P1"}])

    skipped = _table(profile_data(tmp_path), "products").skipped_references

    reasons = {r.reason for r in skipped}
    assert "column customer_id is absent from products" in reasons


def test_columns_parsed_differently_from_the_file_headers_make_the_table_unloadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader that silently mis-detects the header is caught by comparing with the files."""
    write_dimension(tmp_path, "branches", [{"branch_id": "B1"}])
    monkeypatch.setattr(profiler, "_load_table", lambda *_args: {"B1", "S1"})

    profile = profile_data(tmp_path)

    assert profile.tables == ()
    assert "HeaderMismatch" in profile.unloadable_tables[0].reason


# -----------------------------------------------------------------------------
# Files without a header row, bad partition paths and unsafe category text
# -----------------------------------------------------------------------------


def _write_headerless(data_dir: Path, first_row: str) -> None:
    """A second complaints partition whose first line is a data row, not a header."""
    write_partition(
        data_dir,
        "complaints",
        DAY,
        [{"complaint_id": "K1", "creation_date": "2025-01-10 10:00:00"}],
    )
    path = data_dir / "complaints" / "year=2025" / "month=01" / "day=11" / "complaints_20250111.csv"
    path.parent.mkdir(parents=True)
    path.write_text(f"{first_row}\nC4,OTHER\n", encoding="utf-8")


@pytest.mark.parametrize(
    "first_row",
    [f"C3,{SENTINEL}", f"2025-01-11 09:00:00,{SENTINEL} value"],
    ids=["names-that-look-like-identifiers", "values-that-are-not-identifiers"],
)
def test_file_without_a_header_row_never_puts_a_data_value_in_the_report_or_json(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    first_row: str,
) -> None:
    """The first data row must not become column names in any output; the outcome is stated."""
    _write_headerless(tmp_path, first_row)
    report, json_path = tmp_path / "out.md", tmp_path / "out.json"

    with caplog.at_level(logging.DEBUG):
        exit_code = main(
            ["--data-dir", str(tmp_path), "--report", str(report), "--json", str(json_path)]
        )

    text = report.read_text(encoding="utf-8")
    assert exit_code == 0
    for output in (
        text,
        json_path.read_text(encoding="utf-8"),
        caplog.text,
        capsys.readouterr().err,
    ):
        assert SENTINEL not in output
        assert "OTHER" not in output
    assert "InvalidHeader: 1 file(s) of table complaints have no valid header row" in text


def test_inventory_counts_files_without_a_header_row_without_recording_them(
    tmp_path: Path,
) -> None:
    """Only the number of such files is kept; the table's header variants stay clean."""
    _write_headerless(tmp_path, f"C3,{SENTINEL}")

    inventory = scan_table(tmp_path, table("complaints"))

    assert inventory.invalid_headers == 1
    assert len(inventory.header_variants) == 1
    assert all(SENTINEL not in name for v in inventory.header_variants for name in v.columns)


def test_partition_path_with_an_impossible_date_is_measured_not_fatal(tmp_path: Path) -> None:
    """A month 13 directory makes its rows undated, but neither aborts nor hides the table."""
    write_partition(tmp_path, "transactions", DAY, [_transaction("T1", "MXN", "1", "1")])
    bad = tmp_path / "transactions" / "year=2025" / "month=13" / "day=10"
    write_csv(
        bad / "transactions_20251310.csv",
        table("transactions").column_names,
        [_transaction("T2", "MXN", "1", "1")],
    )

    profile = profile_data(tmp_path)
    transactions = _table(profile, "transactions")

    assert profile.unloadable_tables == ()
    assert transactions.key.rows == 2
    assert transactions.inventory.nonconforming_paths == 1
    assert transactions.lateness is not None
    assert transactions.lateness.rows_measured == 1


def test_category_text_cannot_break_out_of_the_markdown_code_span(tmp_path: Path) -> None:
    """A category value with a backtick and a newline is shown on one line, backtick replaced."""
    write_partition(
        tmp_path,
        "complaints",
        DAY,
        [{"complaint_id": "K1", "creation_date": "2025-01-10 10:00:00", "category": "Fees`\nX"}],
    )

    text = render_markdown(profile_data(tmp_path))

    assert "`Fees' X` (1)" in text


def test_identifier_and_reference_columns_are_never_listed_as_categories(tmp_path: Path) -> None:
    """Values of identifier-like and foreign-key columns stay out of the value lists."""
    write_partition(
        tmp_path,
        "complaints",
        DAY,
        [
            {
                "complaint_id": "K1",
                "creation_date": "2025-01-10 10:00:00",
                "category": "Fees",
                "origin_interaction_id": "I1",
                "customer_id": "C1",
            },
        ],
    )

    columns = {c.name: c for c in _table(profile_data(tmp_path), "complaints").columns}

    assert [v.value for v in columns["category"].top_values] == ["Fees"]
    assert columns["origin_interaction_id"].top_values == ()
    assert columns["customer_id"].top_values == ()


# -----------------------------------------------------------------------------
# Boundaries of the measurement rules
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("usd", "within"),
    [("5.145", 1), ("5.155", 0)],
    ids=["2.9-percent-away", "3.1-percent-away"],
)
def test_usd_tolerance_is_three_percent(tmp_path: Path, usd: str, within: int) -> None:
    """An amount 2.9 % from the rate is accepted; one 3.1 % away is not."""
    write_dimension(
        tmp_path,
        "daily_exchange_rates",
        [
            {
                "date": "2025-01-10",
                "source_currency": "MXN",
                "target_currency": "USD",
                "exchange_rate": "0.05",
            }
        ],
    )
    write_partition(tmp_path, "transactions", DAY, [_transaction("T1", "MXN", "100.00", usd)])

    amounts = _facts(profile_data(tmp_path)).usd_amounts

    assert (amounts.within_tolerance, amounts.outside_tolerance) == (within, 1 - within)


def test_only_plain_decimal_numbers_count_as_integers_written_with_a_decimal_point(
    tmp_path: Path,
) -> None:
    """``7.5`` and ``-3.0`` qualify; a trailing dot, a leading dot, exponents and signs do not."""
    values = ["7.5", "-3.0", "7.", ".5", "1e5", "7.5.5", "+3.0", "12"]
    write_dimension(
        tmp_path,
        "customers",
        [{"customer_id": f"C{i}", "credit_score": v} for i, v in enumerate(values)],
    )

    column = next(
        c for c in _table(profile_data(tmp_path), "customers").columns if c.name == "credit_score"
    )

    assert column.integers_written_as_decimals == 2


def test_verdicts_without_an_exchange_rate_table_say_not_assessed(tmp_path: Path) -> None:
    """Without rates the consistency of amount_usd cannot be judged, so the verdict is neutral."""
    write_partition(tmp_path, "transactions", DAY, [_transaction("T1", "MXN", "100", "5")])

    verdicts = {a.statement: a.verdict for a in assess_assumptions(profile_data(tmp_path))}

    assert verdicts["amount_usd is present and consistent with the daily exchange rate"] is (
        Verdict.NOT_ASSESSED
    )


def test_fraud_assumption_is_not_assessed_when_the_label_column_is_absent(
    tmp_path: Path,
) -> None:
    """Without an ``is_fraud`` column no prevalence can be stated, and none is invented."""
    write_csv(
        tmp_path
        / "transactions"
        / "year=2025"
        / "month=01"
        / "day=10"
        / "transactions_20250110.csv",
        ["transaction_id", "transaction_date", "process_date"],
        [
            {
                "transaction_id": "T1",
                "transaction_date": "2025-01-10 08:00:00",
                "process_date": "2025-01-10",
            }
        ],
    )

    profile = profile_data(tmp_path)
    verdicts = {a.statement: a.verdict for a in assess_assumptions(profile)}

    assert _facts(profile).fraud.transactions == 0
    assert verdicts["The fraud label supports a supervised risk model"] is Verdict.NOT_ASSESSED
