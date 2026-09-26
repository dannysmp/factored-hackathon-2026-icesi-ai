"""
Raw Table Loader
================

Overview
--------
Loads the raw CSV files of one source table into a DuckDB connection as text columns, with the
day encoded in each file's path. Shared by the profiler and the cleaning stage so both read the
raw data in exactly the same way.

Scope
-----
In: strict CSV reading, header validation, quoting helpers.
Out: measuring or cleaning the data.

Design Principles
-----------------
- Everything is read as text, so a malformed value is handled by the caller rather than crashing
  the load.
- Reading is strict and the parsed columns are verified against the file headers, because the
  reader can silently mis-detect the header of a malformed file.
- Errors carry only the error class and a fixed hint: the database's own message can quote the
  offending line, and data values must never reach logs or reports.

Runtime Contract
----------------
``load_table(con, data_dir, spec) -> set[str]``, ``reject_invalid_headers(inventory)``,
``verify_headers(loaded, inventory)``; all raise :class:`TableLoadError` on unreadable input.

Limitations
-----------
Files are expected to be comma-separated, quoted with double quotes and UTF-8 encoded.
"""

from __future__ import annotations

# Standard libraries
from pathlib import Path  # Location of the raw files

# Third-party libraries
import duckdb  # Columnar SQL engine over the CSV files

# Local modules
from pipelines.inventory import TableInventory  # Header facts checked before and after loading
from pipelines.sources import Layout, TableSpec  # Layout of each table

# -----------------------------------------------------------------------------
# Errors and quoting
# -----------------------------------------------------------------------------


class TableLoadError(Exception):
    """Raised when a table's files cannot be parsed.

    Only the error class and a fixed hint are kept, never the offending line.
    """


def quote_identifier(identifier: str) -> str:
    """Quote an identifier for DuckDB."""
    return '"' + identifier.replace('"', '""') + '"'


def quote_literal(text: str) -> str:
    """Quote a string literal for DuckDB."""
    return "'" + text.replace("'", "''") + "'"


# -----------------------------------------------------------------------------
# Loading
# -----------------------------------------------------------------------------


def load_table(con: duckdb.DuckDBPyConnection, data_dir: Path, spec: TableSpec) -> set[str]:
    """Load one table as text columns plus ``_partition_date``; return the loaded column names.

    ``_partition_date`` is the day encoded in the file path (null for single-file tables), which
    is the arrival day of the row.

    Raises
    ------
    TableLoadError
        When the files cannot be parsed, for example a row with more fields than the header.
    """
    if spec.layout is Layout.SINGLE_FILE:
        source = str(data_dir / f"{spec.name}.csv")
    else:
        source = str(data_dir / spec.name / "*" / "*" / "*" / "*.csv")
    partition_date = (
        "try_cast(concat("
        "regexp_extract(filename, 'year=([0-9]{4})/', 1), '-', "
        "regexp_extract(filename, 'month=([0-9]{2})/', 1), '-', "
        "regexp_extract(filename, 'day=([0-9]{2})/', 1)) AS DATE)"
    )
    reader = (
        f"read_csv({quote_literal(source)}, header = true, skip = 0, all_varchar = true, "
        "union_by_name = true, filename = true, hive_partitioning = false, "
        "delim = ',', quote = '\"', escape = '\"')"
    )
    try:
        con.execute(
            f"CREATE TABLE {quote_identifier(spec.name)} AS "
            f"SELECT * EXCLUDE (filename), {partition_date} AS _partition_date FROM {reader}"
        )
    except duckdb.Error as exc:
        raise TableLoadError(
            f"{type(exc).__name__}: the files of table {spec.name} could not be parsed"
        ) from None
    described = con.execute(f"DESCRIBE {quote_identifier(spec.name)}").fetchall()
    return {str(row[0]) for row in described}


def reject_invalid_headers(inventory: TableInventory) -> None:
    """Fail without loading when a file does not start with a header row.

    Loading such a file would take its first data row as column names and drop that row. Only
    the number of files is reported, never their content.
    """
    if inventory.invalid_headers:
        raise TableLoadError(
            f"InvalidHeader: {inventory.invalid_headers} file(s) of table {inventory.name} have "
            "no valid header row (names must be identifiers and include the primary key)"
        )


def verify_headers(loaded: set[str], inventory: TableInventory) -> None:
    """Fail when the parsed columns differ from the headers found in the files.

    Guards against a reader that silently mis-detects the header of a malformed file: the
    columns read by the database must be exactly the union of the columns in the file headers.
    """
    expected = {name for variant in inventory.header_variants for name in variant.columns}
    if inventory.header_variants and loaded - {"_partition_date"} != expected:
        raise TableLoadError(
            f"HeaderMismatch: the parsed columns of table {inventory.name} differ from the "
            "file headers and could not be parsed reliably"
        )
