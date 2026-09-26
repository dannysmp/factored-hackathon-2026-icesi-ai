"""
Cleaned Layer Tests
===================

Component: ``pipelines.silver`` and ``pipelines.quality``. Hermetic: every test writes a small
raw dataset whose defects are known (``tests.data_fixture``), runs the cleaning stage into a
temporary directory and checks exact counts, values and bytes.

The update-correctness tests at the end are the proof the design relies on: a late partition, a
re-delivered record, a schema-evolved file and a repeated run all leave the outputs correct and
byte-identical.
"""

from __future__ import annotations

# Standard libraries
import hashlib  # Compare artefacts byte for byte
import json  # Read manifests
from datetime import date  # Partition days
from pathlib import Path  # Temporary dataset locations
from typing import Any  # Query results

# Third-party libraries
import duckdb  # Read Parquet outputs back
import pytest  # Test runner and fixtures

# Local modules
from pipelines import silver as silver_module
from pipelines.outcomes import Status, TableOutcome
from pipelines.quality import render_quality_report
from pipelines.silver import SilverPaths, main, run_silver
from tests.data_fixture import (
    build_clean_dataset,
    valid_row,
    write_dimension,
    write_partition,
)

DAY_ONE, DAY_TWO, DAY_THREE = date(2025, 1, 10), date(2025, 1, 12), date(2025, 1, 11)


def _outcomes(raw: Path, out: Path, **options: Any) -> dict[str, TableOutcome]:
    return {o.table: o for o in run_silver(raw, out, code_version="test", **options)}


def _rows(path: Path, sql: str) -> list[tuple[Any, ...]]:
    return duckdb.connect().execute(sql.format(path=str(path))).fetchall()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artefacts(out: Path) -> dict[str, str]:
    """SHA-256 of every file under ``out``, keyed by relative path."""
    return {
        p.relative_to(out).as_posix(): _digest(p) for p in sorted(out.rglob("*")) if p.is_file()
    }


@pytest.fixture
def clean(tmp_path: Path) -> tuple[Path, Path]:
    """A raw directory holding the clean dataset, and an empty output directory."""
    raw = tmp_path / "raw"
    build_clean_dataset(raw)
    return raw, tmp_path / "out"


# -----------------------------------------------------------------------------
# Typing and output
# -----------------------------------------------------------------------------


def test_clean_dataset_passes_through_without_loss(clean: tuple[Path, Path]) -> None:
    """Every row of a contract-satisfying dataset is kept; nothing is quarantined."""
    raw, out = clean

    outcomes = _outcomes(raw, out)

    assert {n: o.status for n, o in outcomes.items()} == {
        n: Status.BUILT
        for n in ("branches", "customers", "products", "daily_exchange_rates", "transactions")
    }
    counts = {n: o.manifest["counts"] for n, o in outcomes.items() if o.manifest}
    assert counts["transactions"] == {
        "rows_in": 3,
        "rows_out": 3,
        "quarantined": 0,
        "superseded": 0,
    }
    assert counts["customers"]["rows_out"] == 2


def test_columns_are_written_with_their_declared_types(clean: tuple[Path, Path]) -> None:
    """Text is converted to dates, timestamps, numbers and booleans, not left as strings."""
    raw, out = clean
    _outcomes(raw, out)

    described = {
        row[0]: row[1]
        for row in _rows(SilverPaths(out).silver("customers"), "DESCRIBE SELECT * FROM '{path}'")
    }

    assert described["date_of_birth"] == "DATE"
    assert described["registration_date"] == "TIMESTAMP"
    assert described["credit_score"] == "BIGINT"
    assert described["accepts_marketing"] == "BOOLEAN"
    assert described["estimated_monthly_income"].startswith("DECIMAL(12,2)")
    assert described["first_name"] == "VARCHAR"


def test_integer_written_with_a_decimal_point_is_accepted_but_a_fractional_one_is_not(
    clean: tuple[Path, Path],
) -> None:
    """``701.0`` is the integer 701; ``701.5`` cannot be an integer and is quarantined."""
    raw, out = clean
    write_dimension(
        raw,
        "customers",
        [
            valid_row(
                "customers",
                customer_id="C1",
                document_number="D1",
                credit_score="701.0",
                registration_branch_id="B1",
            ),
            valid_row(
                "customers",
                customer_id="C2",
                document_number="D2",
                credit_score="701.5",
                registration_branch_id="B1",
            ),
        ],
    )

    outcome = _outcomes(raw, out)["customers"]

    paths = SilverPaths(out)
    assert _rows(paths.silver("customers"), "SELECT customer_id, credit_score FROM '{path}'") == [
        ("C1", 701)
    ]
    assert outcome.manifest is not None
    assert outcome.manifest["quarantine_reasons"] == {"type:credit_score": 1}


# -----------------------------------------------------------------------------
# Contract checks and quarantine
# -----------------------------------------------------------------------------


def _corrupted_customers(raw: Path) -> None:
    """One good customer and one customer per kind of defect, plus one with two defects."""

    def customer(key: str, **values: str) -> dict[str, str]:
        return valid_row(
            "customers",
            customer_id=key,
            document_number=f"D-{key}",
            registration_branch_id="B1",
            **values,
        )

    write_dimension(
        raw,
        "customers",
        [
            customer("C1"),
            customer("C2", date_of_birth="2025-13-40"),
            customer("C3", credit_score="900"),
            customer("C4", customer_status="Zombie"),
            customer("C5", document_type=""),
            customer("C6", credit_score="900", date_of_birth="not a date"),
        ],
    )


def test_each_kind_of_violation_is_quarantined_with_its_reason(clean: tuple[Path, Path]) -> None:
    """Type, range, allowed-value and required violations each get their own reason code."""
    raw, out = clean
    _corrupted_customers(raw)

    manifest = _outcomes(raw, out)["customers"].manifest

    assert manifest is not None
    assert manifest["quarantine_reasons"] == {
        "range:credit_score": 1,
        "required:document_type": 1,
        "type:date_of_birth": 2,
        "value:customer_status": 1,
    }
    assert _rows(SilverPaths(out).silver("customers"), "SELECT customer_id FROM '{path}'") == [
        ("C1",)
    ]


def test_a_row_with_several_violations_is_quarantined_once_under_its_first_reason(
    clean: tuple[Path, Path],
) -> None:
    """Reasons follow column order, and no row appears twice in quarantine."""
    raw, out = clean
    _corrupted_customers(raw)
    _outcomes(raw, out)

    rows = _rows(
        SilverPaths(out).quarantine("customers"),
        "SELECT customer_id, reason FROM '{path}' ORDER BY customer_id",
    )

    assert [r[0] for r in rows] == ["C2", "C3", "C4", "C5", "C6"]
    assert dict(rows)["C6"] == "type:date_of_birth"  # date_of_birth precedes credit_score


def test_every_raw_row_is_accounted_for(clean: tuple[Path, Path]) -> None:
    """rows in = rows out + superseded + quarantined, for every table."""
    raw, out = clean
    _corrupted_customers(raw)

    for outcome in _outcomes(raw, out).values():
        assert outcome.manifest is not None
        c = outcome.manifest["counts"]
        assert c["rows_in"] == c["rows_out"] + c["superseded"] + c["quarantined"], outcome.table


def test_canonical_spelling_is_applied_before_the_checks(clean: tuple[Path, Path]) -> None:
    """``Mexico`` and ``México`` are one country in the output."""
    raw, out = clean
    write_partition(
        raw,
        "transactions",
        DAY_ONE,
        [
            valid_row(
                "transactions",
                transaction_id="T1",
                product_id="P1",
                customer_id="C1",
                transaction_country="Mexico",
            )
        ],
    )

    _outcomes(raw, out)

    assert _rows(
        SilverPaths(out).silver("transactions"),
        "SELECT transaction_country FROM '{path}' WHERE transaction_id = 'T1'",
    ) == [("México",)]


# -----------------------------------------------------------------------------
# References
# -----------------------------------------------------------------------------


def test_required_reference_to_a_missing_row_quarantines_but_a_branch_reference_only_flags(
    clean: tuple[Path, Path],
) -> None:
    """An orphan customer reference is dropped; an unresolvable branch reference is counted."""
    raw, out = clean
    write_partition(
        raw,
        "transactions",
        DAY_ONE,
        [
            valid_row("transactions", transaction_id="T1", product_id="P1", customer_id="C1"),
            valid_row("transactions", transaction_id="T2", product_id="P1", customer_id="C-GHOST"),
        ],
    )

    manifest = _outcomes(raw, out)["transactions"].manifest

    assert manifest is not None
    assert manifest["quarantine_reasons"] == {"reference:customer_id": 1}
    # Only the two rows that remain are counted: T1 and T3 (T2 was quarantined)
    assert manifest["flagged_references"]["branch_id"] == 2
    assert _rows(
        SilverPaths(out).silver("transactions"),
        "SELECT transaction_id FROM '{path}' ORDER BY transaction_id",
    ) == [("T1",), ("T3",)]


def test_references_to_a_table_that_could_not_be_cleaned_are_reported_unchecked(
    clean: tuple[Path, Path],
) -> None:
    """A skipped customers table means customer references cannot be enforced, and it says so."""
    raw, out = clean
    (raw / "customers.csv").write_text("customer_id,x y\nC1,z\n", encoding="utf-8")

    outcomes = _outcomes(raw, out)

    assert outcomes["customers"].status is Status.SKIPPED
    assert "InvalidHeader" in (outcomes["customers"].reason or "")
    transactions = outcomes["transactions"].manifest
    assert transactions is not None
    assert "customer_id" in transactions["unchecked_references"]
    assert transactions["counts"]["quarantined"] == 0


# -----------------------------------------------------------------------------
# Update correctness: duplicates, late partitions, schema evolution, idempotence
# -----------------------------------------------------------------------------


def test_the_latest_version_of_a_repeated_key_wins_by_last_updated(
    clean: tuple[Path, Path],
) -> None:
    """Two versions of one customer: the later ``last_updated`` is kept, the other counted."""
    raw, out = clean
    write_dimension(
        raw,
        "customers",
        [
            valid_row(
                "customers",
                customer_id="C1",
                document_number="D1",
                first_name="Old",
                last_updated="2025-01-01 00:00:00",
                registration_branch_id="B1",
            ),
            valid_row(
                "customers",
                customer_id="C1",
                document_number="D1",
                first_name="New",
                last_updated="2025-02-01 00:00:00",
                registration_branch_id="B1",
            ),
        ],
    )

    manifest = _outcomes(raw, out)["customers"].manifest

    assert manifest is not None
    assert (manifest["counts"]["rows_out"], manifest["counts"]["superseded"]) == (1, 1)
    assert _rows(SilverPaths(out).silver("customers"), "SELECT first_name FROM '{path}'") == [
        ("New",)
    ]


def test_a_redelivered_record_in_a_later_partition_replaces_the_earlier_one(
    clean: tuple[Path, Path],
) -> None:
    """Tables without ``last_updated`` fall back to the partition day: the later delivery wins."""
    raw, out = clean
    write_partition(
        raw,
        "transactions",
        DAY_THREE,
        [
            valid_row(
                "transactions",
                transaction_id="T1",
                product_id="P1",
                customer_id="C1",
                amount="99.00",
                process_date="2025-01-11",
            )
        ],
    )

    manifest = _outcomes(raw, out)["transactions"].manifest

    assert manifest is not None
    assert manifest["counts"]["superseded"] == 1
    assert _rows(
        SilverPaths(out).silver("transactions"),
        "SELECT amount FROM '{path}' WHERE transaction_id = 'T1'",
    ) == [(99,)]


def test_a_late_partition_is_incorporated_once_and_only_the_affected_table_is_rebuilt(
    clean: tuple[Path, Path],
) -> None:
    """A partition for an earlier day arrives after the first run: the next run picks it up."""
    raw, out = clean
    _outcomes(raw, out)
    write_partition(
        raw,
        "transactions",
        date(2025, 1, 9),
        [
            valid_row(
                "transactions",
                transaction_id="T0",
                product_id="P1",
                customer_id="C1",
                process_date="2025-01-09",
            )
        ],
    )

    second = _outcomes(raw, out)

    assert second["transactions"].status is Status.BUILT
    assert all(o.status is Status.UNCHANGED for n, o in second.items() if n != "transactions")
    assert _rows(
        SilverPaths(out).silver("transactions"),
        "SELECT transaction_id FROM '{path}' ORDER BY transaction_id",
    ) == [("T0",), ("T1",), ("T2",), ("T3",)]


def test_a_column_added_by_a_later_file_is_kept_in_a_sidecar_not_in_the_contract(
    clean: tuple[Path, Path],
) -> None:
    """Unknown columns are preserved beside the cleaned table and named in the manifest."""
    raw, out = clean
    write_partition(
        raw,
        "transactions",
        DAY_THREE,
        [
            {
                **valid_row(
                    "transactions",
                    transaction_id="T9",
                    product_id="P1",
                    customer_id="C1",
                    process_date="2025-01-11",
                ),
                "loyalty_tier": "gold",
            }
        ],
        extra_columns=("loyalty_tier",),
    )

    manifest = _outcomes(raw, out)["transactions"].manifest

    assert manifest is not None
    assert manifest["extra_columns"] == ["loyalty_tier"]
    assert "loyalty_tier" not in {
        r[0]
        for r in _rows(SilverPaths(out).silver("transactions"), "DESCRIBE SELECT * FROM '{path}'")
    }
    assert _rows(
        SilverPaths(out).extras("transactions"),
        "SELECT transaction_id, loyalty_tier FROM '{path}' WHERE loyalty_tier IS NOT NULL",
    ) == [("T9", "gold")]


def test_removing_the_extra_column_removes_the_sidecar(clean: tuple[Path, Path]) -> None:
    """A stale sidecar never outlives the data that produced it."""
    raw, out = clean
    extra = write_partition(
        raw,
        "transactions",
        DAY_THREE,
        [
            {
                **valid_row(
                    "transactions",
                    transaction_id="T9",
                    product_id="P1",
                    customer_id="C1",
                    process_date="2025-01-11",
                ),
                "loyalty_tier": "gold",
            }
        ],
        extra_columns=("loyalty_tier",),
    )
    _outcomes(raw, out)
    assert SilverPaths(out).extras("transactions").exists()

    extra.unlink()
    _outcomes(raw, out)

    assert not SilverPaths(out).extras("transactions").exists()


def test_running_twice_changes_nothing(clean: tuple[Path, Path]) -> None:
    """The second run rebuilds nothing and leaves every artefact byte-identical."""
    raw, out = clean
    _outcomes(raw, out)
    before = _artefacts(out)

    second = _outcomes(raw, out)

    assert all(o.status is Status.UNCHANGED for o in second.values())
    assert _artefacts(out) == before


def test_a_forced_rebuild_reproduces_every_artefact_byte_for_byte(clean: tuple[Path, Path]) -> None:
    """Determinism: rebuilding from the same inputs yields identical Parquet files and manifests."""
    raw, out = clean
    _outcomes(raw, out)
    before = _artefacts(out)

    forced = _outcomes(raw, out, force=True)

    assert all(o.status is Status.BUILT for o in forced.values())
    assert _artefacts(out) == before


def test_a_new_code_version_rebuilds_but_produces_the_same_data(clean: tuple[Path, Path]) -> None:
    """The manifest records the code version; the data bytes do not depend on it."""
    raw, out = clean
    _outcomes(raw, out)
    parquet = {k: v for k, v in _artefacts(out).items() if k.endswith(".parquet")}

    rebuilt = {o.table: o for o in run_silver(raw, out, code_version="another")}

    assert all(o.status is Status.BUILT for o in rebuilt.values())
    assert {k: v for k, v in _artefacts(out).items() if k.endswith(".parquet")} == parquet
    manifest = json.loads(SilverPaths(out).manifest("customers").read_text(encoding="utf-8"))
    assert manifest["code_version"] == "another"


def test_a_damaged_output_is_rebuilt_even_when_the_inputs_are_unchanged(
    clean: tuple[Path, Path],
) -> None:
    """The manifest is trusted only while the output still matches its recorded digest."""
    raw, out = clean
    _outcomes(raw, out)
    SilverPaths(out).silver("customers").write_bytes(b"corrupted")

    second = _outcomes(raw, out)

    assert second["customers"].status is Status.BUILT
    assert _rows(SilverPaths(out).silver("customers"), "SELECT count(*) FROM '{path}'") == [(2,)]


# -----------------------------------------------------------------------------
# Command line and report
# -----------------------------------------------------------------------------


def test_command_line_builds_everything_and_writes_the_report(clean: tuple[Path, Path]) -> None:
    """The entry point exits 0, writes artefacts and a report listing the quarantine reasons."""
    raw, out = clean
    _corrupted_customers(raw)
    report = out.parent / "quality.md"

    exit_code = main(
        ["--raw", str(raw), "--out", str(out), "--report", str(report), "--code-version", "test"]
    )

    text = report.read_text(encoding="utf-8")
    assert exit_code == 0
    assert "| customers | range:credit_score | 1 |" in text
    assert "| customers | 6 | 1 | 0 | 5 |" in text


def test_command_line_exits_with_one_when_a_table_could_not_be_processed(
    clean: tuple[Path, Path],
) -> None:
    """A skipped table fails the run loudly and is named in the report."""
    raw, out = clean
    (raw / "customers.csv").write_text("customer_id,x y\nC1,z\n", encoding="utf-8")
    report = out.parent / "quality.md"

    exit_code = main(
        ["--raw", str(raw), "--out", str(out), "--report", str(report), "--code-version", "test"]
    )

    assert exit_code == 1
    assert "| customers | InvalidHeader:" in report.read_text(encoding="utf-8")


def test_report_lists_counts_and_reason_codes_but_never_data_values(
    clean: tuple[Path, Path],
) -> None:
    """Only counts and codes appear: the rejected customers' identifiers are absent."""
    raw, out = clean
    _corrupted_customers(raw)

    text = render_quality_report(list(run_silver(raw, out, code_version="test")))

    assert "required:document_type" in text
    for value in ("D-C2", "D-C3", "Zombie", "not a date"):
        assert value not in text
    assert text.endswith("\n")


def test_report_is_a_pure_function_of_the_outcomes(clean: tuple[Path, Path]) -> None:
    """Rendering the same outcomes twice gives identical text."""
    raw, out = clean
    outcomes = list(run_silver(raw, out, code_version="test"))

    assert render_quality_report(outcomes) == render_quality_report(outcomes)


# -----------------------------------------------------------------------------
# Code version and empty runs
# -----------------------------------------------------------------------------


def test_the_code_version_defaults_to_the_current_commit(clean: tuple[Path, Path]) -> None:
    """Without ``--code-version`` the manifests record the short commit id of the checkout."""
    raw, out = clean

    main(["--raw", str(raw), "--out", str(out), "--report", str(out.parent / "q.md")])

    manifest = json.loads(SilverPaths(out).manifest("branches").read_text(encoding="utf-8"))
    assert manifest["code_version"] == silver_module._git_version()
    assert manifest["code_version"] != ""


def test_code_version_is_unknown_when_git_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside a repository (or without git) the version is a stable placeholder, not an error."""

    def missing(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError

    monkeypatch.setattr("pipelines.silver.subprocess.run", missing)

    assert silver_module._git_version() == "unknown"


def test_an_empty_run_renders_an_empty_report_and_exits_zero(tmp_path: Path) -> None:
    """With no raw files nothing is built and nothing fails."""
    report = tmp_path / "q.md"

    exit_code = main(
        [
            "--raw",
            str(tmp_path / "raw"),
            "--out",
            str(tmp_path / "out"),
            "--report",
            str(report),
            "--code-version",
            "test",
        ]
    )

    text = report.read_text(encoding="utf-8")
    assert exit_code == 0
    assert "Contract version n/a" in text


# -----------------------------------------------------------------------------
# Boundaries and ordering
# -----------------------------------------------------------------------------


def test_numeric_range_bounds_are_inclusive(clean: tuple[Path, Path]) -> None:
    """300 and 850 are inside the credit-score range; 299 and 851 are not."""
    raw, out = clean
    scores = {"C1": "300", "C2": "299", "C3": "850", "C4": "851"}
    write_dimension(
        raw,
        "customers",
        [
            valid_row(
                "customers",
                customer_id=key,
                document_number=f"D-{key}",
                credit_score=score,
                registration_branch_id="B1",
            )
            for key, score in scores.items()
        ],
    )

    manifest = _outcomes(raw, out)["customers"].manifest

    assert manifest is not None
    assert manifest["quarantine_reasons"] == {"range:credit_score": 2}
    assert _rows(
        SilverPaths(out).silver("customers"),
        "SELECT customer_id FROM '{path}' ORDER BY customer_id",
    ) == [("C1",), ("C3",)]


def test_quarantine_rows_are_ordered_by_reason_then_key(clean: tuple[Path, Path]) -> None:
    """The quarantine file has a fixed order, so it is reproducible and easy to read."""
    raw, out = clean
    _corrupted_customers(raw)
    _outcomes(raw, out)

    order = _rows(
        SilverPaths(out).quarantine("customers"), "SELECT customer_id, reason FROM '{path}'"
    )

    assert order == [
        ("C3", "range:credit_score"),
        ("C5", "required:document_type"),
        ("C2", "type:date_of_birth"),
        ("C6", "type:date_of_birth"),
        ("C4", "value:customer_status"),
    ]


# -----------------------------------------------------------------------------
# Unreadable files never leak their content
# -----------------------------------------------------------------------------


def test_a_ragged_file_skips_the_table_without_quoting_its_content(
    clean: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    """A row with more fields than the header is reported by class only; other tables build."""
    raw, out = clean
    secret = "SENTINEL_VALUE_9F3A"
    (raw / "branches.csv").write_text(
        f"branch_id,branch_code\nB1,S1\nB2,S2,{secret}\n", encoding="utf-8"
    )
    report = out.parent / "quality.md"

    with caplog.at_level("DEBUG"):
        outcomes = _outcomes(raw, out)
        exit_code = main(
            [
                "--raw",
                str(raw),
                "--out",
                str(out),
                "--report",
                str(report),
                "--code-version",
                "test",
            ]
        )

    assert outcomes["branches"].status is Status.SKIPPED
    assert outcomes["customers"].status is Status.BUILT
    assert exit_code == 1
    assert secret not in caplog.text
    assert secret not in report.read_text(encoding="utf-8")
    assert secret not in (outcomes["branches"].reason or "")


def test_report_describes_the_data_not_the_run(clean: tuple[Path, Path]) -> None:
    """A rebuild and a no-op run of the same data render the same report."""
    raw, out = clean
    built = render_quality_report(list(run_silver(raw, out, code_version="test")))

    unchanged = render_quality_report(list(run_silver(raw, out, code_version="test")))

    assert built == unchanged
