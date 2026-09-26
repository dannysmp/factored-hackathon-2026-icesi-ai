"""
Raw Data Profiler
=================

Overview
--------
Measures the raw source tables against what the data dictionary claims: row counts, file and
header conformance, key uniqueness, missing values, values that do not parse as their declared
type, text-encoding damage, referential integrity, arrival lateness and the workload facts
(fraud prevalence, USD amount consistency, transcript availability, complaint categories) that
decide which workflow to build and how to evaluate it.

Scope
-----
In: loading the CSV files into a throwaway DuckDB database and running read-only queries.
Out: cleaning, contracts and any write to the data (those are pipeline stages) and the
Markdown presentation (``pipelines.profile_report``).

Design Principles
-----------------
- Everything is read as text first (``all_varchar``), so a malformed value is counted rather
  than crashing the load, and each dictionary claim is checked explicitly.
- The database lives in a temporary directory and is discarded; the raw files are never
  modified.
- Output is deterministic: no timestamps, sorted collections, stable rounding.
- Identifiers interpolated into SQL come from the static table registry and are always quoted;
  no value from the data is ever interpolated.

Runtime Contract
----------------
``profile_data(data_dir, specs=TABLES) -> DataProfile``
``python -m pipelines.profile --data-dir data/raw --report reports/data-profile.md``

Limitations
-----------
Distinct counts use DuckDB's approximate estimator; values shown for low-cardinality columns
are exact. The text-encoding check catches the common UTF-8-read-as-Latin-1 pattern and the
replacement character, not every possible corruption.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command-line interface
import json  # Machine-readable copy of the profile
import logging  # Progress events on stderr
import sys  # Exit codes and log stream
import tempfile  # Throwaway database directory
import time  # Load timing for progress events
from collections.abc import Sequence  # Type of the specs argument
from dataclasses import asdict  # JSON form of the profile
from pathlib import Path  # Input and output locations
from typing import Any  # Row values returned by the database driver

# Third-party libraries
import duckdb  # Columnar SQL engine over the CSV files

# Local modules
from pipelines.inventory import inventory_digest, scan_table  # Filesystem-level facts
from pipelines.profile_models import (  # Result objects
    ColumnProfile,
    ComplaintFacts,
    ContactFacts,
    DataProfile,
    DomainFacts,
    ForeignKeyProfile,
    FraudFacts,
    KeyProfile,
    LatenessProfile,
    MonthlyCount,
    TableProfile,
    UsdAmountFacts,
    ValueCount,
)
from pipelines.profile_report import render_markdown  # Markdown presentation
from pipelines.sources import TABLES, Column, Layout, TableKind, TableSpec  # Table registry

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

# Values are listed for a column only when it has at most this many distinct values.
MAX_CATEGORICAL_VALUES = 25
# Estimated distinct count above which the exact value listing is not even attempted.
ESTIMATE_LIMIT = 40
# Columns declared at least this long hold free text, where encoding damage is likely.
FREE_TEXT_MIN_LENGTH = 30
# Rows returned for a distribution of reasons or categories.
DISTRIBUTION_LIMIT = 40
# Lateness thresholds, in days after the event.
LATE_AFTER_DAYS = 7
VERY_LATE_AFTER_DAYS = 30
# The stated USD amount is converted at fixed rates rather than the daily table, so it deviates
# from amount x daily rate by up to about 2 % (measured on a sample). It is accepted within 3 %,
# and never by less than one cent.
FX_RELATIVE_TOLERANCE = 0.03
FX_ABSOLUTE_TOLERANCE = 0.01
# Damage left by decoding UTF-8 as Latin-1 (for example "Ã©" or "â€") and the replacement
# character.
ENCODING_DAMAGE_PATTERN = r"Ã[\x{0080}-\x{00BF}]|\x{FFFD}|â€"
# A value written like a decimal number.
DECIMAL_POINT_PATTERN = r"^-?[0-9]+\.[0-9]+$"


class ProfileError(Exception):
    """Raised when a table cannot be loaded or a declared key column is missing."""


# -----------------------------------------------------------------------------
# SQL helpers
# -----------------------------------------------------------------------------


def _quote_identifier(identifier: str) -> str:
    """Quote an identifier for DuckDB."""
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(text: str) -> str:
    """Quote a string literal for DuckDB."""
    return "'" + text.replace("'", "''") + "'"


def _row(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    """Run a query that returns exactly one row."""
    row = con.execute(sql).fetchone()
    if row is None:
        raise ProfileError(f"query returned no row: {sql[:120]}")
    return tuple(row)


def _integers(con: duckdb.DuckDBPyConnection, sql: str) -> list[int]:
    """Run a single-row query of counts and return the values as integers."""
    return [int(value) for value in _row(con, sql)]


def _value_counts(
    con: duckdb.DuckDBPyConnection, sql: str, limit: int = DISTRIBUTION_LIMIT
) -> tuple[ValueCount, ...]:
    """Run a ``SELECT value, count`` query and wrap the first ``limit`` rows."""
    rows = con.execute(f"{sql} LIMIT {limit}").fetchall()
    return tuple(ValueCount(str(value), int(count)) for value, count in rows)


def _distribution_sql(table: str, column: str) -> str:
    """SQL listing the values of ``column`` with their counts, most frequent first."""
    return (
        f"SELECT coalesce({column}, '<null>'), count(*) FROM {table} GROUP BY 1 ORDER BY 2 DESC, 1"
    )


def _parseable_predicate(column: Column) -> str | None:
    """SQL predicate that is true when a non-null text value parses as the declared type."""
    name = _quote_identifier(column.name)
    dtype = column.dtype.upper()
    if dtype in {"DATE", "TIMESTAMP", "TIME"}:
        return f"try_cast({name} AS {dtype}) IS NOT NULL"
    if dtype == "INTEGER" or dtype.startswith("DECIMAL"):
        return f"try_cast({name} AS DOUBLE) IS NOT NULL"
    if dtype == "BOOLEAN":
        return f"{name} IN ('True', 'False')"
    return None


def _is_free_text(column: Column) -> bool:
    """True for columns that hold human-written text, where encoding damage is likely."""
    dtype = column.dtype.upper()
    if dtype == "TEXT":
        return True
    declared_length = dtype.removeprefix("VARCHAR(").removesuffix(")")
    return dtype.startswith("VARCHAR(") and int(declared_length) >= FREE_TEXT_MIN_LENGTH


# -----------------------------------------------------------------------------
# Loading
# -----------------------------------------------------------------------------


def _load_table(con: duckdb.DuckDBPyConnection, data_dir: Path, spec: TableSpec) -> set[str]:
    """Load one table as text columns plus ``_partition_date``; return the loaded column names.

    ``_partition_date`` is the day encoded in the file path (null for single-file tables), which
    is the arrival day used by the lateness measurement.
    """
    if spec.layout is Layout.SINGLE_FILE:
        source = str(data_dir / f"{spec.name}.csv")
    else:
        source = str(data_dir / spec.name / "*" / "*" / "*" / "*.csv")
    partition_date = (
        "make_date("
        "try_cast(regexp_extract(filename, 'year=([0-9]{4})/', 1) AS INTEGER), "
        "try_cast(regexp_extract(filename, 'month=([0-9]{2})/', 1) AS INTEGER), "
        "try_cast(regexp_extract(filename, 'day=([0-9]{2})/', 1) AS INTEGER))"
    )
    reader = (
        f"read_csv({_quote_literal(source)}, header = true, all_varchar = true, "
        "union_by_name = true, filename = true, hive_partitioning = false, "
        "delim = ',', quote = '\"', escape = '\"')"
    )
    try:
        con.execute(
            f"CREATE TABLE {_quote_identifier(spec.name)} AS "
            f"SELECT * EXCLUDE (filename), {partition_date} AS _partition_date FROM {reader}"
        )
    except duckdb.Error as exc:
        raise ProfileError(f"cannot load table {spec.name}: {exc}") from exc
    described = con.execute(f"DESCRIBE {_quote_identifier(spec.name)}").fetchall()
    return {str(row[0]) for row in described}


# -----------------------------------------------------------------------------
# Per-table measurements
# -----------------------------------------------------------------------------


def _key_profile(con: duckdb.DuckDBPyConnection, spec: TableSpec, present: set[str]) -> KeyProfile:
    """Measure primary-key uniqueness and whether repeated keys carry identical content.

    Content is compared without ``process_date``, so the same record delivered again in a later
    partition counts as an identical re-delivery rather than a conflicting update.
    """
    missing = [key for key in spec.primary_key if key not in present]
    if missing:
        raise ProfileError(f"table {spec.name} lacks key column(s) {missing}")
    table = _quote_identifier(spec.name)
    key = ", ".join(_quote_identifier(name) for name in spec.primary_key)
    content = ", ".join(
        f"coalesce({_quote_identifier(column.name)}, '<null>')"
        for column in spec.columns
        if column.name in present and column.name != "process_date"
    )
    rows, distinct = _integers(
        con,
        f"SELECT count(*), (SELECT count(*) FROM (SELECT 1 FROM {table} GROUP BY {key})) "
        f"FROM {table}",
    )
    groups, identical, conflicting = _integers(
        con,
        "WITH repeated AS ("
        f"SELECT count(DISTINCT md5(concat_ws('|', {content}))) AS variants "
        f"FROM {table} GROUP BY {key} HAVING count(*) > 1) "
        "SELECT count(*), count(*) FILTER (WHERE variants = 1), "
        "count(*) FILTER (WHERE variants > 1) FROM repeated",
    )
    return KeyProfile(rows, distinct, groups, identical, conflicting)


def _column_aggregates(column: Column, index: int) -> list[str]:
    """SQL aggregates measuring one column; always five, in the order read back."""
    name = _quote_identifier(column.name)
    parseable = _parseable_predicate(column)
    damage = _quote_literal(ENCODING_DAMAGE_PATTERN)
    decimals = _quote_literal(DECIMAL_POINT_PATTERN)
    unparseable = (
        f"count(*) FILTER (WHERE {name} IS NOT NULL AND NOT ({parseable}))" if parseable else "0"
    )
    written_as_decimals = (
        f"count(*) FILTER (WHERE regexp_matches({name}, {decimals}))"
        if column.dtype.upper() == "INTEGER"
        else "0"
    )
    encoding_damage = (
        f"count(*) FILTER (WHERE regexp_matches({name}, {damage}))"
        if _is_free_text(column)
        else "0"
    )
    return [
        f"count(*) FILTER (WHERE {name} IS NULL) AS nulls_{index}",
        f"{unparseable} AS unparseable_{index}",
        f"{written_as_decimals} AS decimals_{index}",
        f"{encoding_damage} AS damaged_{index}",
        f"approx_count_distinct({name}) AS distinct_{index}",
    ]


def _column_profiles(
    con: duckdb.DuckDBPyConnection, spec: TableSpec, present: set[str], rows: int
) -> tuple[ColumnProfile, ...]:
    """Measure nulls, unparseable values, encoding damage and low-cardinality value counts."""
    columns = [column for column in spec.columns if column.name in present]
    if not columns:
        return ()
    table = _quote_identifier(spec.name)
    aggregates = [sql for i, column in enumerate(columns) for sql in _column_aggregates(column, i)]
    measured = _integers(con, f"SELECT {', '.join(aggregates)} FROM {table}")
    profiles: list[ColumnProfile] = []
    for index, column in enumerate(columns):
        nulls, unparseable, decimals, damaged, distinct = measured[index * 5 : index * 5 + 5]
        listable = (
            column.dtype.upper().startswith(("VARCHAR", "BOOLEAN"))
            and column.name not in spec.primary_key
        )
        top_values: tuple[ValueCount, ...] = ()
        if listable and distinct <= ESTIMATE_LIMIT:
            values = _value_counts(
                con,
                _distribution_sql(table, _quote_identifier(column.name)),
                MAX_CATEGORICAL_VALUES + 1,
            )
            if len(values) <= MAX_CATEGORICAL_VALUES:
                top_values = values
        profiles.append(
            ColumnProfile(
                name=column.name,
                dtype=column.dtype,
                declared_nullable=column.nullable,
                rows=rows,
                nulls=nulls,
                unparseable=unparseable,
                integers_written_as_decimals=decimals,
                text_encoding_suspects=damaged,
                distinct_estimate=distinct,
                top_values=top_values,
            )
        )
    return tuple(profiles)


def _foreign_keys(
    con: duckdb.DuckDBPyConnection,
    spec: TableSpec,
    present: set[str],
    loaded: dict[str, set[str]],
) -> tuple[ForeignKeyProfile, ...]:
    """Count references that point at no existing row, for every declared foreign key."""
    results: list[ForeignKeyProfile] = []
    for fk in spec.foreign_keys:
        reference_available = fk.ref_column in loaded.get(fk.ref_table, set())
        if fk.column not in present or not reference_available:
            continue
        column, referenced = _quote_identifier(fk.column), _quote_identifier(fk.ref_column)
        known = (
            f"SELECT {referenced} FROM {_quote_identifier(fk.ref_table)} "
            f"WHERE {referenced} IS NOT NULL"
        )
        checked, orphans = _integers(
            con,
            f"SELECT count(*) FILTER (WHERE {column} IS NOT NULL), "
            f"count(*) FILTER (WHERE {column} IS NOT NULL AND {column} NOT IN ({known})) "
            f"FROM {_quote_identifier(spec.name)}",
        )
        results.append(ForeignKeyProfile(fk.column, fk.ref_table, fk.ref_column, checked, orphans))
    return tuple(results)


def _optional_int(value: Any) -> int | None:
    """Convert a nullable database value to an integer."""
    return None if value is None else int(value)


def _optional_rounded(value: Any) -> float | None:
    """Convert a nullable database value to a float rounded for stable output."""
    return None if value is None else round(float(value), 2)


def _lateness(
    con: duckdb.DuckDBPyConnection, spec: TableSpec, present: set[str]
) -> LatenessProfile | None:
    """Measure how long after the event each row arrived, in partition days."""
    if spec.kind is not TableKind.FACT or "process_date" not in present:
        return None
    table = _quote_identifier(spec.name)
    event = spec.event_time_column
    process_day = "try_cast(process_date AS DATE)"
    if event is None or event not in present:
        measured, mismatches = _integers(
            con,
            "SELECT count(*) FILTER (WHERE _partition_date IS NOT NULL), "
            "count(*) FILTER (WHERE _partition_date IS NOT NULL "
            f"AND {process_day} IS NOT NULL AND _partition_date <> {process_day}) "
            f"FROM {table}",
        )
        return LatenessProfile(measured, mismatches, None, None, None, None, None, 0, 0, 0)
    event_day = f"try_cast({_quote_identifier(event)} AS TIMESTAMP)::DATE"
    row = _row(
        con,
        f"WITH stamped AS (SELECT {event_day} AS event_day, "
        f"_partition_date AS partition_day, {process_day} AS process_day FROM {table}), "
        "lagged AS (SELECT partition_day - event_day AS lag, partition_day, process_day "
        "FROM stamped WHERE partition_day IS NOT NULL AND event_day IS NOT NULL) "
        "SELECT count(*), "
        "count(*) FILTER (WHERE process_day IS NOT NULL AND partition_day <> process_day), "
        "min(lag), quantile_cont(lag, 0.5), quantile_cont(lag, 0.95), "
        "quantile_cont(lag, 0.99), max(lag), "
        "count(*) FILTER (WHERE lag < 0), "
        f"count(*) FILTER (WHERE lag > {LATE_AFTER_DAYS}), "
        f"count(*) FILTER (WHERE lag > {VERY_LATE_AFTER_DAYS}) FROM lagged",
    )
    return LatenessProfile(
        rows_measured=int(row[0]),
        partition_process_date_mismatches=int(row[1]),
        lag_min=_optional_int(row[2]),
        lag_p50=_optional_rounded(row[3]),
        lag_p95=_optional_rounded(row[4]),
        lag_p99=_optional_rounded(row[5]),
        lag_max=_optional_int(row[6]),
        stamped_after_partition=int(row[7]),
        lagged_over_7_days=int(row[8]),
        lagged_over_30_days=int(row[9]),
    )


# -----------------------------------------------------------------------------
# Workload facts
# -----------------------------------------------------------------------------


def _fraud_facts(con: duckdb.DuckDBPyConnection) -> FraudFacts:
    """Fraud label prevalence, overall and by month of the transaction."""
    transactions, positives = _integers(
        con, "SELECT count(*), count(*) FILTER (WHERE is_fraud = 'True') FROM transactions"
    )
    rows = con.execute(
        "SELECT strftime(try_cast(transaction_date AS TIMESTAMP), '%Y-%m') AS month, "
        "count(*), count(*) FILTER (WHERE is_fraud = 'True') FROM transactions "
        "WHERE try_cast(transaction_date AS TIMESTAMP) IS NOT NULL GROUP BY 1 ORDER BY 1"
    ).fetchall()
    by_month = tuple(MonthlyCount(str(m), int(n), int(p)) for m, n, p in rows)
    return FraudFacts(transactions, positives, by_month)


def _usd_amount_facts(con: duckdb.DuckDBPyConnection, with_rates: bool) -> UsdAmountFacts:
    """Compare the stated USD amount with the local amount converted at the daily rate."""
    (present,) = _integers(
        con, "SELECT count(*) FILTER (WHERE amount_usd IS NOT NULL) FROM transactions"
    )
    if not with_rates:
        return UsdAmountFacts(present, 0, 0, present)
    tolerance = f"greatest({FX_ABSOLUTE_TOLERANCE}, {FX_RELATIVE_TOLERANCE} * abs(usd_amount))"
    _, without_rate, within = _integers(
        con,
        'WITH rates AS (SELECT try_cast("date" AS DATE) AS day, source_currency AS currency, '
        "min(try_cast(exchange_rate AS DOUBLE)) AS rate FROM daily_exchange_rates "
        "WHERE target_currency = 'USD' GROUP BY 1, 2), "
        "stated AS (SELECT try_cast(amount AS DOUBLE) AS local_amount, "
        "try_cast(amount_usd AS DOUBLE) AS usd_amount, currency, "
        "try_cast(transaction_date AS TIMESTAMP)::DATE AS day FROM transactions "
        "WHERE amount_usd IS NOT NULL), "
        "converted AS (SELECT stated.local_amount, stated.usd_amount, "
        "CASE WHEN stated.currency = 'USD' THEN 1.0 ELSE rates.rate END AS rate "
        "FROM stated LEFT JOIN rates "
        "ON rates.day = stated.day AND rates.currency = stated.currency) "
        "SELECT count(*), "
        "count(*) FILTER (WHERE rate IS NULL OR local_amount IS NULL OR usd_amount IS NULL), "
        "count(*) FILTER (WHERE rate IS NOT NULL AND local_amount IS NOT NULL "
        f"AND usd_amount IS NOT NULL AND abs(usd_amount - local_amount * rate) <= {tolerance}) "
        "FROM converted",
    )
    return UsdAmountFacts(present, within, present - without_rate - within, without_rate)


def _contact_facts(con: duckdb.DuckDBPyConnection, loaded: dict[str, set[str]]) -> ContactFacts:
    """Contact-centre volume, reasons and how many interactions have a transcript."""
    interactions = flagged = transcripts = transcript_interactions = 0
    reasons: tuple[ValueCount, ...] = ()
    categories: tuple[ValueCount, ...] = ()
    contact_columns = ("has_transcript", "contact_reason", "reason_category")
    if _has_columns(loaded, "call_center_interactions", *contact_columns):
        table = "call_center_interactions"
        interactions, flagged = _integers(
            con,
            f"SELECT count(*), count(*) FILTER (WHERE has_transcript = 'True') FROM {table}",
        )
        reasons = _value_counts(con, _distribution_sql(table, "contact_reason"))
        categories = _value_counts(con, _distribution_sql(table, "reason_category"))
    if _has_columns(loaded, "call_transcripts", "interaction_id"):
        transcripts, transcript_interactions = _integers(
            con, "SELECT count(*), count(DISTINCT interaction_id) FROM call_transcripts"
        )
    return ContactFacts(
        interactions, flagged, transcripts, transcript_interactions, reasons, categories
    )


def _complaint_facts(con: duckdb.DuckDBPyConnection) -> ComplaintFacts:
    """Complaint volume, repeat complainers, SLA breaches and the category distribution."""
    complaints, repeat, breached = _integers(
        con,
        "SELECT count(*), count(*) FILTER (WHERE is_repeat_complainer = 'True'), "
        "count(*) FILTER (WHERE sla_breached = 'True') FROM complaints",
    )
    categories = _value_counts(con, _distribution_sql("complaints", "category"))
    return ComplaintFacts(complaints, repeat, breached, categories)


def _has_columns(loaded: dict[str, set[str]], table: str, *columns: str) -> bool:
    """True when ``table`` was loaded and contains every one of ``columns``."""
    return table in loaded and set(columns) <= loaded[table]


def _domain_facts(con: duckdb.DuckDBPyConnection, loaded: dict[str, set[str]]) -> DomainFacts:
    """Collect the facts that drive workflow selection.

    A fact whose source table or columns are absent (for example after schema drift) is left
    empty instead of failing the whole profile.
    """
    fraud = FraudFacts(0, 0, ())
    if _has_columns(loaded, "transactions", "is_fraud", "transaction_date"):
        fraud = _fraud_facts(con)
    usd = UsdAmountFacts(0, 0, 0, 0)
    if _has_columns(loaded, "transactions", "amount", "amount_usd", "currency", "transaction_date"):
        rates = _has_columns(
            loaded,
            "daily_exchange_rates",
            "date",
            "source_currency",
            "target_currency",
            "exchange_rate",
        )
        usd = _usd_amount_facts(con, rates)
    complaints = ComplaintFacts(0, 0, 0, ())
    if _has_columns(loaded, "complaints", "is_repeat_complainer", "sla_breached", "category"):
        complaints = _complaint_facts(con)
    return DomainFacts(fraud, usd, _contact_facts(con, loaded), complaints)


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def profile_data(data_dir: Path, specs: Sequence[TableSpec] = TABLES) -> DataProfile:
    """Profile the raw data under ``data_dir``.

    Parameters
    ----------
    data_dir : Path
        Root of the raw data (dimension CSV files and partitioned fact directories).
    specs : Sequence[TableSpec]
        Tables to profile; tables without files are skipped with a warning.

    Returns
    -------
    DataProfile
        Immutable, deterministic result.

    Raises
    ------
    ProfileError
        When a table cannot be loaded or lacks its key column.
    """
    inventories = {spec.name: scan_table(data_dir, spec) for spec in specs}
    available = [spec for spec in specs if inventories[spec.name].files > 0]
    for spec in specs:
        if inventories[spec.name].files == 0:
            logger.warning("profile_table_missing table=%s", spec.name)
    with tempfile.TemporaryDirectory() as workdir:
        con = duckdb.connect(str(Path(workdir) / "profile.duckdb"))
        con.execute("SET preserve_insertion_order = false")
        # Load every available table first, because foreign-key checks need the referenced tables
        loaded: dict[str, set[str]] = {}
        for spec in available:
            started = time.monotonic()
            loaded[spec.name] = _load_table(con, data_dir, spec)
            elapsed = time.monotonic() - started
            logger.info("profile_table_loaded table=%s seconds=%.1f", spec.name, elapsed)
        tables: list[TableProfile] = []
        for spec in available:
            present = loaded[spec.name]
            key = _key_profile(con, spec, present)
            declared = set(spec.column_names)
            tables.append(
                TableProfile(
                    name=spec.name,
                    inventory=inventories[spec.name],
                    expected_rows=spec.expected_rows,
                    key=key,
                    extra_columns=tuple(sorted(present - declared - {"_partition_date"})),
                    missing_columns=tuple(n for n in spec.column_names if n not in present),
                    columns=_column_profiles(con, spec, present, key.rows),
                    foreign_keys=_foreign_keys(con, spec, present, loaded),
                    lateness=_lateness(con, spec, present),
                )
            )
            logger.info("profile_table_profiled table=%s rows=%d", spec.name, key.rows)
        facts = _domain_facts(con, loaded)
        con.close()
    return DataProfile(inventory_digest(data_dir, tuple(available)), tuple(tables), facts)


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Profile the raw data and write the Markdown and JSON reports.

    Returns
    -------
    int
        Process exit code: 0 on success, 1 when the data cannot be profiled.
    """
    parser = argparse.ArgumentParser(description="Profile the raw source tables.")
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/raw"), help="raw data directory"
    )
    parser.add_argument(
        "--report", type=Path, default=Path("reports/data-profile.md"), help="Markdown output"
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=Path("reports/data-profile.json"),
        dest="json_path",
        help="JSON output",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr
    )
    try:
        profile = profile_data(args.data_dir)
    except ProfileError as exc:
        logger.error("profile_failed reason=%s", exc)
        return 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_markdown(profile), encoding="utf-8")
    args.json_path.write_text(
        json.dumps(asdict(profile), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    logger.info("profile_written report=%s json=%s", args.report, args.json_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
