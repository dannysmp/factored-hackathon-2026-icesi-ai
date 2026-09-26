"""
Raw Data Inventory Module
=========================

Overview
--------
Filesystem-level facts about the raw data directory: which files exist, how big they are,
whether partition paths follow the declared layout, whether files carry a byte-order mark and
whether every file's header matches the declared columns. It also computes a digest of the
inventory so a report can state exactly which snapshot it describes.

Scope
-----
In: listing files, reading first lines, path and header checks, inventory digest.
Out: parsing row content (see ``pipelines.profile``).

Design Principles
-----------------
- Only file names, sizes and the first line of each file are read; no row data is loaded.
- Output ordering is sorted so results are identical across runs and machines.

Runtime Contract
----------------
``table_files(data_dir, spec) -> list[Path]``
``scan_table(data_dir, spec) -> TableInventory``
``inventory_digest(data_dir, specs) -> str``

Limitations
-----------
The digest covers relative paths and sizes, not file contents: a same-size edit is not detected.
"""

from __future__ import annotations

# Standard libraries
import csv  # Parse a header line that may contain quoted names
import hashlib  # SHA-256 digest of the inventory
import re  # Partition path pattern
from collections import Counter  # Group files by identical header
from dataclasses import dataclass  # Immutable result objects
from datetime import date, timedelta  # Calendar arithmetic for missing partition days
from pathlib import Path  # Filesystem access

# Local modules
from pipelines.sources import Layout, TableSpec  # Declared layout and expected columns

# -----------------------------------------------------------------------------
# Constants and types
# -----------------------------------------------------------------------------

# A column name is an identifier; a first line that is not is data, not a header.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# UTF-8 byte-order mark that some exporters prepend to every file.
_BOM = b"\xef\xbb\xbf"

# Partition directory of a daily-partitioned file: year=YYYY/month=MM/day=DD/<name>_YYYYMMDD.csv
_PARTITION_PATH = re.compile(
    r"year=(?P<y>\d{4})/month=(?P<m>\d{2})/day=(?P<d>\d{2})/(?P<stem>[a-z_]+)_(?P<stamp>\d{8})\.csv$"
)


@dataclass(frozen=True, slots=True)
class HeaderVariant:
    """A distinct header found in the files of one table and how many files carry it."""

    columns: tuple[str, ...]
    files: int


@dataclass(frozen=True, slots=True)
class TableInventory:
    """Filesystem facts for one table."""

    name: str
    files: int
    total_bytes: int
    bom_files: int
    undecodable_headers: int
    partition_days: int
    first_partition: str | None
    last_partition: str | None
    missing_partition_days: int
    nonconforming_paths: int
    header_variants: tuple[HeaderVariant, ...]
    files_matching_declared_header: int
    invalid_headers: int = 0


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def table_files(data_dir: Path, spec: TableSpec) -> list[Path]:
    """List the CSV files that hold ``spec``, sorted by path.

    Returns
    -------
    list[Path]
        One file for single-file tables; every partition file for daily-partitioned tables.
        Empty when the table is absent.
    """
    if spec.layout is Layout.SINGLE_FILE:
        candidate = data_dir / f"{spec.name}.csv"
        return [candidate] if candidate.is_file() else []
    return sorted((data_dir / spec.name).glob("*/*/*/*.csv"))


def _read_header(path: Path) -> tuple[tuple[str, ...] | None, bool]:
    """Return the parsed header of ``path`` (None when undecodable) and whether it has a BOM."""
    with path.open("rb") as handle:
        first_line = handle.readline()
    has_bom = first_line.startswith(_BOM)
    try:
        text = first_line.removeprefix(_BOM).decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError:
        return None, has_bom
    parsed = next(csv.reader([text]), [])
    return tuple(parsed), has_bom


def _is_header(columns: tuple[str, ...], spec: TableSpec) -> bool:
    """True when ``columns`` looks like the header of ``spec``'s files rather than a data row.

    Every name must be an identifier and the primary-key columns must be present. A file that
    lacks its header row would otherwise put its first data row into the report as column names.
    """
    return all(_IDENTIFIER.fullmatch(name) for name in columns) and set(spec.primary_key) <= set(
        columns
    )


def _partition_day(relative: str, table_name: str) -> tuple[date | None, bool]:
    """Return the partition date encoded in ``relative`` and whether the path is conforming."""
    match = _PARTITION_PATH.search(relative)
    if match is None or match["stem"] != table_name:
        return None, False
    try:
        day = date(int(match["y"]), int(match["m"]), int(match["d"]))
    except ValueError:
        return None, False
    # The file name must carry the same date as the directories above it.
    return day, match["stamp"] == day.strftime("%Y%m%d")


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def scan_table(data_dir: Path, spec: TableSpec) -> TableInventory:
    """Collect filesystem-level facts for one table.

    Parameters
    ----------
    data_dir : Path
        Root of the raw data.
    spec : TableSpec
        Declared layout and columns of the table.

    Returns
    -------
    TableInventory
        Counts, partition span, path conformance and header variants. Files whose first line is
        not a header (it has a name that is not an identifier, or lacks the primary key) are
        counted as invalid and their content is never recorded.
    """
    files = table_files(data_dir, spec)
    headers: Counter[tuple[str, ...]] = Counter()
    bom_files = undecodable = invalid = nonconforming = 0
    days: set[date] = set()
    total_bytes = 0

    # Read every file's first line and validate its partition path
    for path in files:
        total_bytes += path.stat().st_size
        header, has_bom = _read_header(path)
        bom_files += has_bom
        if header is None:
            undecodable += 1
        elif not _is_header(header, spec):
            invalid += 1
        else:
            headers[header] += 1
        if spec.layout is Layout.DAILY_PARTITIONS:
            day, conforming = _partition_day(path.relative_to(data_dir).as_posix(), spec.name)
            nonconforming += not conforming
            if day is not None:
                days.add(day)

    # Count calendar days inside the partition span that have no file
    missing = 0
    if days:
        span = (max(days) - min(days)).days + 1
        missing = span - len(days)

    variants = tuple(
        HeaderVariant(columns=columns, files=count)
        for columns, count in sorted(headers.items(), key=lambda item: (-item[1], item[0]))
    )
    declared = spec.column_names
    return TableInventory(
        name=spec.name,
        files=len(files),
        total_bytes=total_bytes,
        bom_files=bom_files,
        undecodable_headers=undecodable,
        partition_days=len(days),
        first_partition=min(days).isoformat() if days else None,
        last_partition=max(days).isoformat() if days else None,
        missing_partition_days=missing,
        nonconforming_paths=nonconforming,
        header_variants=variants,
        files_matching_declared_header=headers.get(declared, 0),
        invalid_headers=invalid,
    )


def missing_days(days: set[date]) -> list[date]:
    """Return the calendar days between the first and last of ``days`` that are absent."""
    if not days:
        return []
    first, last = min(days), max(days)
    return [
        first + timedelta(offset)
        for offset in range((last - first).days + 1)
        if first + timedelta(offset) not in days
    ]


def inventory_digest(data_dir: Path, specs: tuple[TableSpec, ...]) -> str:
    """Return a SHA-256 digest over the sorted relative paths and sizes of every table file."""
    digest = hashlib.sha256()
    for spec in specs:
        for path in table_files(data_dir, spec):
            entry = f"{path.relative_to(data_dir).as_posix()}\t{path.stat().st_size}\n"
            digest.update(entry.encode("utf-8"))
    return digest.hexdigest()
