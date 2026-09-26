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


# -----------------------------------------------------------------------------
# Freshness: dependencies, content, stale outputs, damaged artefacts
# -----------------------------------------------------------------------------


def _only_customer_c1(raw: Path) -> None:
    write_dimension(
        raw,
        "customers",
        [
            valid_row(
                "customers", customer_id="C1", document_number="D1", registration_branch_id="B1"
            )
        ],
    )


def test_a_change_in_a_referenced_table_rebuilds_the_tables_that_reference_it(
    clean: tuple[Path, Path],
) -> None:
    """Removing customer C2 makes C2's products orphans, though the products file is unchanged."""
    raw, out = clean
    _outcomes(raw, out)
    _only_customer_c1(raw)

    second = _outcomes(raw, out)

    assert second["customers"].status is Status.BUILT
    assert second["products"].status is Status.BUILT
    assert second["transactions"].status is Status.BUILT
    assert second["branches"].status is Status.UNCHANGED
    manifest = second["products"].manifest
    assert manifest is not None
    assert manifest["quarantine_reasons"] == {"reference:customer_id": 1}
    assert _rows(SilverPaths(out).silver("products"), "SELECT product_id FROM '{path}'") == [
        ("P1",)
    ]


def test_a_run_over_unchanged_inputs_rebuilds_no_dependent_table(
    clean: tuple[Path, Path],
) -> None:
    """Dependency tracking does not turn a no-op run into a rebuild."""
    raw, out = clean
    _outcomes(raw, out)

    second = _outcomes(raw, out)

    assert {o.status for o in second.values()} == {Status.UNCHANGED}


def test_a_same_size_edit_of_an_input_file_rebuilds_the_table(clean: tuple[Path, Path]) -> None:
    """Freshness follows the content of the files, not only their names and sizes."""
    raw, out = clean
    _outcomes(raw, out)
    path = raw / "customers.csv"
    before = path.read_bytes()
    original = valid_row("customers", customer_id="C1")["first_name"].encode()
    after = before.replace(original, b"X" * len(original), 1)
    assert after != before
    assert len(after) == len(before)
    path.write_bytes(after)

    second = _outcomes(raw, out)

    assert second["customers"].status is Status.BUILT


def test_a_skipped_table_leaves_no_outputs_of_an_earlier_build(
    clean: tuple[Path, Path],
) -> None:
    """A table that stops being readable must not keep serving its previous outputs."""
    raw, out = clean
    _outcomes(raw, out)
    paths = SilverPaths(out)
    assert paths.silver("branches").exists()
    (raw / "branches.csv").write_text("branch_id\nB1,extra,fields\n", encoding="utf-8")

    second = _outcomes(raw, out)

    assert second["branches"].status is Status.SKIPPED
    assert not paths.silver("branches").exists()
    assert not paths.manifest("branches").exists()


def _damage_and_rerun(raw: Path, out: Path, table: str, artefact: Path) -> None:
    """Corrupt one output of ``table`` and assert the next run rebuilds it identically."""
    first = _outcomes(raw, out)[table].manifest
    assert first is not None
    assert artefact.exists()
    artefact.write_bytes(b"corrupted")

    second = _outcomes(raw, out)

    assert second[table].status is Status.BUILT
    assert second[table].manifest == first


def test_a_damaged_quarantine_table_is_rebuilt(clean: tuple[Path, Path]) -> None:
    """The quarantine is verified against the manifest like the main output."""
    raw, out = clean
    _corrupted_customers(raw)

    _damage_and_rerun(raw, out, "customers", SilverPaths(out).quarantine("customers"))


def test_a_damaged_sidecar_is_rebuilt(clean: tuple[Path, Path]) -> None:
    """The sidecar of unknown columns is verified against the manifest too."""
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

    _damage_and_rerun(raw, out, "transactions", SilverPaths(out).extras("transactions"))


# -----------------------------------------------------------------------------
# Typing edge cases
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["NaN", "inf", "-inf", "1e30", "9007199254740993", "9223372036854775808", "abc", "7 01"],
)
def test_an_integer_column_never_fails_the_table_on_a_hostile_value(
    clean: tuple[Path, Path], text: str
) -> None:
    """Values that are not exact integers are quarantined as type violations, row by row."""
    raw, out = clean
    write_dimension(
        raw,
        "customers",
        [
            valid_row(
                "customers", customer_id="C1", document_number="D1", registration_branch_id="B1"
            ),
            valid_row(
                "customers",
                customer_id="C2",
                document_number="D2",
                credit_score=text,
                registration_branch_id="B1",
            ),
        ],
    )

    outcome = _outcomes(raw, out)["customers"]

    assert outcome.status is Status.BUILT
    assert outcome.manifest is not None
    assert outcome.manifest["counts"]["quarantined"] == 1
    assert _rows(SilverPaths(out).silver("customers"), "SELECT customer_id FROM '{path}'") == [
        ("C1",)
    ]


def test_an_environment_failure_stops_the_run_instead_of_skipping_the_table(
    clean: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only faults of the files skip a table; a failing disk or out-of-memory error propagates."""
    raw, out = clean

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise duckdb.IOException("disk full")

    monkeypatch.setattr(silver_module, "_deduplicate", broken)

    with pytest.raises(duckdb.IOException):
        _outcomes(raw, out)


def test_a_newer_invalid_version_leaves_the_older_valid_version_in_place(
    clean: tuple[Path, Path],
) -> None:
    """A newer version that fails the contract is quarantined; the older valid one stays."""
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
                credit_score="900",
                last_updated="2025-02-01 00:00:00",
                registration_branch_id="B1",
            ),
        ],
    )

    manifest = _outcomes(raw, out)["customers"].manifest

    assert manifest is not None
    assert manifest["quarantine_reasons"] == {"range:credit_score": 1}
    assert _rows(SilverPaths(out).silver("customers"), "SELECT first_name FROM '{path}'") == [
        ("Old",)
    ]


def test_equal_versions_are_ordered_by_content_so_the_choice_is_deterministic(
    clean: tuple[Path, Path],
) -> None:
    """Two versions identical in key, timestamp and partition resolve the same way every run."""
    raw, out = clean
    rows = [
        valid_row(
            "customers",
            customer_id="C1",
            document_number="D1",
            first_name=name,
            last_updated="2025-01-01 00:00:00",
            registration_branch_id="B1",
        )
        for name in ("Alpha", "Bravo")
    ]
    write_dimension(raw, "customers", rows)
    _outcomes(raw, out)
    first = _rows(SilverPaths(out).silver("customers"), "SELECT first_name FROM '{path}'")
    write_dimension(raw, "customers", rows[::-1])

    _outcomes(raw, out)

    assert _rows(SilverPaths(out).silver("customers"), "SELECT first_name FROM '{path}'") == first


def test_only_capitalised_booleans_are_accepted(clean: tuple[Path, Path]) -> None:
    """``true`` is not the source's spelling of a boolean and is quarantined as a type violation."""
    raw, out = clean
    write_dimension(
        raw,
        "customers",
        [
            valid_row(
                "customers",
                customer_id="C1",
                document_number="D1",
                accepts_marketing="false",
                registration_branch_id="B1",
            )
        ],
    )

    manifest = _outcomes(raw, out)["customers"].manifest

    assert manifest is not None
    assert manifest["quarantine_reasons"] == {"type:accepts_marketing": 1}


def test_a_value_with_more_decimals_than_the_column_allows_is_rejected_not_rounded(
    clean: tuple[Path, Path],
) -> None:
    """``estimated_monthly_income`` has two decimals; a third is a type violation."""
    raw, out = clean
    write_dimension(
        raw,
        "customers",
        [
            valid_row(
                "customers",
                customer_id="C1",
                document_number="D1",
                estimated_monthly_income="1000.005",
                registration_branch_id="B1",
            )
        ],
    )

    manifest = _outcomes(raw, out)["customers"].manifest

    assert manifest is not None
    assert manifest["quarantine_reasons"] == {"type:estimated_monthly_income": 1}


def test_the_code_version_is_marked_when_the_working_tree_has_uncommitted_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outputs built from modified code are never taken for outputs of the recorded commit."""
    answers = {"rev-parse": "abc1234", "status": " M pipelines/silver.py"}
    monkeypatch.setattr(silver_module, "_git", lambda *args: answers[args[0]])

    assert silver_module._git_version() == "abc1234-dirty"

    answers["status"] = ""
    assert silver_module._git_version() == "abc1234"


def test_a_build_that_fails_midway_leaves_no_manifest_of_the_earlier_build(
    clean: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a crash no manifest vouches for outputs that no longer match the inputs."""
    raw, out = clean
    _outcomes(raw, out)
    _only_customer_c1(raw)

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise duckdb.IOException("disk full")

    monkeypatch.setattr(silver_module, "_deduplicate", broken)
    with pytest.raises(duckdb.IOException):
        _outcomes(raw, out)

    assert not SilverPaths(out).manifest("customers").exists()


def test_adding_a_missing_referenced_row_returns_the_orphans_to_the_cleaned_table(
    clean: tuple[Path, Path],
) -> None:
    """The dependency rule works in both directions: the orphans are recovered on the next run."""
    raw, out = clean
    full = (raw / "customers.csv").read_bytes()
    _only_customer_c1(raw)
    _outcomes(raw, out)
    (raw / "customers.csv").write_bytes(full)

    second = _outcomes(raw, out)

    assert second["products"].status is Status.BUILT
    assert _rows(
        SilverPaths(out).silver("products"), "SELECT product_id FROM '{path}' ORDER BY 1"
    ) == [("P1",), ("P2",)]


def test_a_different_engine_version_rebuilds_the_table(
    clean: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outputs are only trusted for the database engine version that produced them."""
    raw, out = clean
    _outcomes(raw, out)
    monkeypatch.setattr(duckdb, "__version__", "0.0.0-other")

    second = _outcomes(raw, out)

    assert all(o.status is Status.BUILT for o in second.values())


def test_a_sidecar_the_manifest_does_not_list_triggers_a_rebuild(clean: tuple[Path, Path]) -> None:
    """A stray sidecar next to a table without unknown columns is stale and removed."""
    raw, out = clean
    _outcomes(raw, out)
    paths = SilverPaths(out)
    paths.extras("customers").parent.mkdir(parents=True, exist_ok=True)
    paths.extras("customers").write_bytes(b"stale")

    second = _outcomes(raw, out)

    assert second["customers"].status is Status.BUILT
    assert not paths.extras("customers").exists()


@pytest.mark.parametrize("text", ["9007199254740993.0", " 700 ", "1e3", "700.5"])
def test_integer_text_is_matched_exactly(clean: tuple[Path, Path], text: str) -> None:
    """Padding, exponents and decimal forms beyond exact range are not integers."""
    raw, out = clean
    write_dimension(
        raw,
        "customers",
        [
            valid_row(
                "customers",
                customer_id="C1",
                document_number="D1",
                credit_score=text,
                registration_branch_id="B1",
            )
        ],
    )

    manifest = _outcomes(raw, out)["customers"].manifest

    assert manifest is not None
    assert manifest["quarantine_reasons"] == {"type:credit_score": 1}


def test_a_value_beyond_the_declared_scale_is_rejected_at_any_length(
    clean: tuple[Path, Path],
) -> None:
    """Eleven decimals are as invalid as three; nothing is rounded silently."""
    raw, out = clean
    write_dimension(
        raw,
        "customers",
        [
            valid_row(
                "customers",
                customer_id="C1",
                document_number="D1",
                estimated_monthly_income="1000.00000000004",
                registration_branch_id="B1",
            )
        ],
    )

    manifest = _outcomes(raw, out)["customers"].manifest

    assert manifest is not None
    assert manifest["quarantine_reasons"] == {"type:estimated_monthly_income": 1}


def test_rows_quarantined_for_an_orphan_reference_hold_their_typed_values_as_text(
    clean: tuple[Path, Path],
) -> None:
    """The documented rule: orphan rows are typed before they are quarantined."""
    raw, out = clean
    write_dimension(
        raw,
        "products",
        [
            valid_row(
                "products",
                product_id="P1",
                customer_id="ORPHAN",
                product_number="N1",
                opening_branch_id="B1",
                credit_limit="10.5",
                has_linked_app="False",
            )
        ],
    )

    _outcomes(raw, out)

    assert _rows(
        SilverPaths(out).quarantine("products"),
        "SELECT customer_id, credit_limit, has_linked_app FROM '{path}'",
    ) == [("ORPHAN", "10.50", "false")]
