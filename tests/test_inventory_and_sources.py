"""
Inventory and Source Registry Tests
===================================

Components: ``pipelines.inventory`` and ``pipelines.sources``. Hermetic: only file names, sizes
and first lines are involved, all written into a temporary directory.
"""

from __future__ import annotations

# Standard libraries
from datetime import date  # Partition days
from pathlib import Path  # Temporary dataset locations

# Third-party libraries
import pytest  # Test runner and fixtures

# Local modules
from pipelines.inventory import inventory_digest, missing_days, scan_table, table_files
from pipelines.sources import TABLES, Layout, TableKind, TableSpec, table
from tests.data_fixture import build_dispute_dataset, write_csv, write_partition


def test_registry_describes_every_source_table_once() -> None:
    """Thirteen uniquely named tables: seven facts, five dimensions and one reference table."""
    names = [spec.name for spec in TABLES]

    assert len(names) == 13
    assert len(set(names)) == 13
    assert sum(spec.kind is not TableKind.FACT for spec in TABLES) == 6


@pytest.mark.parametrize("spec", TABLES, ids=lambda spec: spec.name)
def test_registry_entries_are_internally_consistent(spec: TableSpec) -> None:
    """Keys, foreign keys and event columns all point at declared columns and tables."""
    columns = set(spec.column_names)
    assert set(spec.primary_key) <= columns
    assert spec.expected_rows > 0
    if spec.event_time_column is not None:
        assert spec.event_time_column in columns
    for fk in spec.foreign_keys:
        assert fk.column in columns
        assert fk.ref_column in set(table(fk.ref_table).column_names)


def test_primary_key_columns_are_never_nullable() -> None:
    """The dictionary's key columns are NOT NULL."""
    for spec in TABLES:
        nullable = {c.name for c in spec.columns if c.nullable}
        assert not set(spec.primary_key) & nullable, spec.name


def test_unknown_table_raises_key_error() -> None:
    """Looking up a table that is not in the registry fails with its name."""
    with pytest.raises(KeyError, match="orders"):
        table("orders")


def test_scan_reports_partition_span_missing_days_and_byte_order_marks(tmp_path: Path) -> None:
    """The dataset has one hole between its two partition days."""
    build_dispute_dataset(tmp_path)

    inventory = scan_table(tmp_path, table("transactions"))

    assert inventory.files == 2
    assert (inventory.first_partition, inventory.last_partition) == ("2025-01-10", "2025-01-12")
    assert (inventory.partition_days, inventory.missing_partition_days) == (2, 1)
    assert inventory.bom_files == 2
    assert inventory.nonconforming_paths == 0
    assert inventory.undecodable_headers == 0
    assert inventory.total_bytes > 0


def test_scan_of_an_absent_table_is_empty(tmp_path: Path) -> None:
    """No files means zero everything, not an error."""
    inventory = scan_table(tmp_path, table("customers"))

    assert (inventory.files, inventory.first_partition, inventory.header_variants) == (0, None, ())
    assert table_files(tmp_path, table("transactions")) == []


def test_single_file_tables_are_found_by_name(tmp_path: Path) -> None:
    """Dimension tables live in ``<name>.csv`` at the top of the data directory."""
    path = write_csv(tmp_path / "branches.csv", table("branches").column_names, [])

    assert table_files(tmp_path, table("branches")) == [path]


@pytest.mark.parametrize(
    "relative",
    [
        "transactions/year=2025/month=01/day=10/other_20250110.csv",
        "transactions/year=2025/month=01/day=10/transactions_20250111.csv",
        "transactions/year=2025/month=13/day=10/transactions_20251310.csv",
    ],
    ids=["wrong-table-name", "file-date-differs-from-directory", "impossible-date"],
)
def test_paths_that_break_the_layout_are_counted(tmp_path: Path, relative: str) -> None:
    """A partition file whose path does not follow the declared layout is non-conforming."""
    write_csv(tmp_path / relative, table("transactions").column_names, [])

    assert scan_table(tmp_path, table("transactions")).nonconforming_paths == 1


def test_header_that_is_not_utf8_is_counted_not_fatal(tmp_path: Path) -> None:
    """Undecodable headers are reported so encoding damage is visible in the inventory."""
    path = tmp_path / "transactions/year=2025/month=01/day=10/transactions_20250110.csv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"transaction_id,caf\xe9\n")

    inventory = scan_table(tmp_path, table("transactions"))

    assert inventory.undecodable_headers == 1
    assert inventory.header_variants == ()


def test_a_header_with_an_undeclared_column_before_a_declared_one_is_invalid(
    tmp_path: Path,
) -> None:
    """A data row whose values happen to be identifier-shaped and cover the primary key must not
    be mistaken for a header just because it also includes an undeclared name — unless that name
    comes strictly after every declared one, it looks like a data row, not a genuine header with
    a trailing extension column."""
    path = tmp_path / "transactions/year=2025/month=01/day=10/transactions_20250110.csv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"transaction_id,unexpected_extra_column,customer_id\n")

    inventory = scan_table(tmp_path, table("transactions"))

    assert inventory.header_variants == ()
    assert inventory.invalid_headers == 1


def test_a_header_with_a_trailing_undeclared_column_is_still_valid(tmp_path: Path) -> None:
    """An undeclared name appearing only after every declared one is tolerated: a provider's own
    later extension column, not evidence the row is data rather than a header."""
    path = tmp_path / "transactions/year=2025/month=01/day=10/transactions_20250110.csv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"transaction_id,customer_id,trailing_extra_column\n")

    inventory = scan_table(tmp_path, table("transactions"))

    assert inventory.invalid_headers == 0
    assert len(inventory.header_variants) == 1


def test_files_without_a_byte_order_mark_are_not_counted(tmp_path: Path) -> None:
    """Only files that start with the mark contribute to the byte-order-mark count."""
    path = tmp_path / "branches.csv"
    path.write_bytes(b"branch_id\nB1\n")

    assert scan_table(tmp_path, table("branches")).bom_files == 0


def test_missing_days_lists_every_gap_between_first_and_last() -> None:
    """Gaps are reported in calendar order; an empty set has none."""
    days = {date(2025, 1, 1), date(2025, 1, 4), date(2025, 1, 5)}

    assert missing_days(days) == [date(2025, 1, 2), date(2025, 1, 3)]
    assert missing_days(set()) == []


def test_digest_is_stable_and_changes_with_the_file_set(tmp_path: Path) -> None:
    """The snapshot digest identifies the inventory: same files, same digest; new file, new one."""
    build_dispute_dataset(tmp_path)
    before = inventory_digest(tmp_path, TABLES)

    assert inventory_digest(tmp_path, TABLES) == before
    write_partition(tmp_path, "transactions", date(2025, 1, 11), [])
    assert inventory_digest(tmp_path, TABLES) != before


def test_layouts_match_the_kind_of_table() -> None:
    """Facts are partitioned; dimensions and the reference table are single files."""
    for spec in TABLES:
        expected = Layout.DAILY_PARTITIONS if spec.kind is TableKind.FACT else Layout.SINGLE_FILE
        assert spec.layout is expected, spec.name
