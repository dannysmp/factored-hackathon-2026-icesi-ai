"""
Seed Loader
===========

Overview
--------
Loads the built operational seed (``pipelines.ops_seed``'s gold output) into the serving
store's ``customers``, ``products`` and ``transactions`` tables, and records the seed's
reference date in ``ops_meta.data_as_of`` so the running service's domain calendar can read it
without a ``DATA_AS_OF_DATE`` override (ADR-15).

Scope
-----
In: reading the seed's Parquet files and its manifest, truncating and reloading the four
serving-store tables and ``ops_meta`` in one transaction, the command line
``python -m app.persistence.load_seed``.
Out: building the seed itself (``pipelines.ops_seed``), running the migrations that create the
tables (``app.persistence.migrate``, which must already have run against this database).

Design Principles
------------------
- Truncates ``cases``, ``transactions``, ``products`` and ``customers`` (reverse foreign-key
  order) before loading, so re-running the loader against a changed seed never leaves stale rows
  behind; ``CASCADE`` covers a foreign key this loader does not itself enumerate.
- ``cases`` is truncated but never repopulated: a case is a live artifact of the case-service
  tool, not seed content (``pipelines.ops_seed``'s own Limitations record why).
- One transaction: the truncate, every insert and the ``ops_meta`` write commit together, or none
  of them do, so a failed load never leaves the store half-seeded.
- The reference date loaded into ``ops_meta`` is the manifest's own, not re-derived: the seed
  that was built is the seed that gets loaded, with no second chance to disagree with itself.

Runtime Contract
-----------------
``load_seed(dsn, gold_dir) -> LoadResult``
The command line ``python -m app.persistence.load_seed``.

Limitations
-----------
Assumes the migrations have already been applied (``app.persistence.migrate``); a store without
the four tables and ``ops_meta`` fails with the driver's own error, not a friendlier one.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command line
import json  # Reading the seed's manifest
import logging  # Progress events, never print
import sys  # Log stream
from collections.abc import Sequence  # Type of the parsed argv
from dataclasses import dataclass  # Immutable result object
from pathlib import Path  # Locations of the seed's output

# Third-party libraries
import duckdb  # Reads the seed's own Parquet files
import psycopg  # Serving-store driver

# Local modules
from app.config import ConfigError, load_settings  # The one validated source of DATABASE_URL
from pipelines.ops_seed import (  # The seed's own output names
    CUSTOMERS_NAME,
    MANIFEST_NAME,
    PRODUCTS_NAME,
    TRANSACTIONS_NAME,
)
from pipelines.raw import quote_literal  # Safe SQL string literals

logger = logging.getLogger(__name__)

_TABLES_IN_LOAD_ORDER = (CUSTOMERS_NAME, PRODUCTS_NAME, TRANSACTIONS_NAME)


@dataclass(frozen=True, slots=True)
class LoadResult:
    """What the load wrote."""

    rows: dict[str, int]
    data_as_of: str


def _read_table(gold_dir: Path, name: str) -> tuple[tuple[str, ...], list[tuple[object, ...]]]:
    """Columns and rows of one seed output, in the file's own column order.

    Raises
    ------
    FileNotFoundError
        When the seed has not been built.
    """
    path = gold_dir / name
    if not path.is_file():
        raise FileNotFoundError(f"seed output {name} not found; build the seed first")
    con = duckdb.connect()
    try:
        cursor = con.execute(f"SELECT * FROM read_parquet({quote_literal(str(path))})")
        columns = tuple(column[0] for column in cursor.description)
        return columns, cursor.fetchall()
    finally:
        con.close()


def _read_reference_date(gold_dir: Path) -> str:
    """The seed's own reference date, from its manifest.

    Raises
    ------
    FileNotFoundError
        When the seed has not been built.
    ValueError
        When the manifest carries no reference date.
    """
    path = gold_dir / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(f"seed manifest {MANIFEST_NAME} not found; build the seed first")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    reference_date = manifest.get("reference_date")
    if not isinstance(reference_date, str) or not reference_date:
        raise ValueError(f"{path} carries no reference_date")
    return reference_date


def load_seed(dsn: str, gold_dir: Path) -> LoadResult:
    """Truncate the serving store and load the seed, in one transaction.

    Raises
    ------
    FileNotFoundError
        When the seed's Parquet output or manifest is missing.
    ValueError
        When the manifest is malformed.
    psycopg.Error
        When the load itself fails; nothing already written commits.
    """
    tables = [(name, *_read_table(gold_dir, name)) for name in _TABLES_IN_LOAD_ORDER]
    reference_date = _read_reference_date(gold_dir)

    rows: dict[str, int] = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE cases, transactions, products, customers CASCADE")
        for name, columns, data in tables:
            table = Path(name).stem
            column_list = ", ".join(columns)
            placeholders = ", ".join(["%s"] * len(columns))
            if data:
                cur.executemany(
                    f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})", data
                )
            rows[table] = len(data)
        cur.execute(
            "INSERT INTO ops_meta (key, value) VALUES ('data_as_of', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            [reference_date],
        )
        conn.commit()
    return LoadResult(rows=rows, data_as_of=reference_date)


def main(argv: Sequence[str] | None = None) -> int:
    """Load the seed from the command line.

    Returns
    -------
    int
        0 on success, whatever exit code an uncaught driver error would otherwise raise.
    """
    parser = argparse.ArgumentParser(description="Load the built operational seed into Postgres.")
    parser.add_argument("--gold", type=Path, default=Path("data/gold/ops_seed"))
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: DATABASE_URL)")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr
    )
    if args.dsn:
        dsn = args.dsn
    else:
        try:
            dsn = load_settings().require_database_url().get_secret_value()
        except ConfigError as exc:
            parser.error(str(exc))
    result = load_seed(dsn, args.gold)
    logger.info(
        "seed_loaded rows=%s data_as_of=%s",
        ",".join(f"{table}:{count}" for table, count in sorted(result.rows.items())),
        result.data_as_of,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
