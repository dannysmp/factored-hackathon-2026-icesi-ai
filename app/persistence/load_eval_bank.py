"""
Evaluation Bank Loader
======================

Overview
--------
Loads ``data/gold/eval_bank``'s frozen scenario rows into the serving store's ``customers``,
``products`` and ``transactions`` tables, additively alongside an already-loaded operational seed
(``app.persistence.load_seed``) and never truncating, since a full evaluation run needs both
sources present in the store at once (a golden-set case grounds on the operational seed or on
``eval_bank``, whichever its condition needs).

Scope
-----
In: reading ``eval_bank``'s Parquet files and manifest, upserting ``customers``, ``products`` and
``transactions`` by primary key, inserting a transaction only when its ``customer_id`` and
``product_id`` both already resolve in the store after the customer and product upsert. A
transaction scenario authored with a deliberately dangling reference (``pipelines.eval_bank``'s
"orphan" scenario) is quarantined (skipped, logged and counted), never inserted with a relaxed or
placeholder foreign key, so the store's referential integrity is never weakened for one fixture
row's sake. The command line ``python -m app.persistence.load_eval_bank``.
Out: loading the operational seed itself (``app.persistence.load_seed``, which must already have
run against this database: this loader is additive and assumes the tables it writes to already
exist and, for a full run, already carry the seed); building ``eval_bank``'s own Parquet output
(``pipelines.eval_bank``); which ``eval_bank`` customer an orphan-anchored golden-set case
authenticates as (a choice made by the golden-set definitions in ``evals.golden.adversarial``).

Design Principles
-----------------
- **Additive, not a second truncate.** ``app.persistence.load_seed`` owns the one full-reset
  entry point for the store; this loader only ever inserts or upserts on top of whatever is
  already there, so running it after ``load_seed`` never discards the seed's own rows, and
  running it more than once is idempotent: every table upserts by primary key, so a row already
  present is overwritten with the same values rather than duplicated or errored on.
- **A dangling reference is quarantined, never given a relaxed or placeholder foreign key.** A
  transaction row whose ``customer_id`` or ``product_id`` does not resolve in the store (checked
  after this loader's own ``customers``/``products`` upsert has run, so both real seed rows and
  eval_bank's own rows count) is skipped rather than inserted. A golden-set case that needs to
  describe such a transaction authenticates as a real ``eval_bank`` customer who does not own it
  instead; it never authenticates as the dangling row's own declared owner, because doing so
  would mint that owner for real and make the "row" genuinely findable, silently contradicting
  the case's own premise.
- **Digest-verified before anything loads.** The same discipline ``app.persistence.load_seed``
  applies to the operational seed's output: a gold directory whose Parquet files changed since
  the manifest was written is refused, not silently loaded as if it still matched.
- **No clock.** This loader has nothing to do with a reference date: ``eval_bank``'s manifest
  carries none (every scenario's date is a fixed literal), and ``ops_meta.data_as_of`` stays
  exactly what ``load_seed`` wrote from the operational seed.

Runtime Contract
----------------
``load_eval_bank(dsn, gold_dir) -> EvalBankLoadResult``
The command line ``python -m app.persistence.load_eval_bank``.

Limitations
-----------
Assumes the migrations have already been applied and, for a full evaluation run, that
``app.persistence.load_seed`` has already loaded the operational seed into the same database;
running this loader alone against an empty store loads only ``eval_bank``'s own real customer and
product, and quarantines every transaction scenario that references anything else. Nothing
enforces that ordering beyond the order in which the build targets run the two loaders.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command line
import hashlib  # Verifies each output against its manifest digest before loading
import json  # Reading the eval bank's manifest
import logging  # Progress and quarantine events, never print
from collections.abc import Sequence  # Type of the parsed argv
from dataclasses import dataclass  # Immutable result object
from pathlib import Path  # Locations of the eval bank's output
from typing import Any  # The parsed manifest

# Third-party libraries
import duckdb  # Reads the eval bank's own Parquet files
import psycopg  # Serving-store driver

# Local modules
from app.config import ConfigError, load_settings  # The one validated source of a DSN
from app.observability.logging import configure_logging_from_settings  # Structured logging
from pipelines.eval_bank import (  # The eval bank's own output names
    CUSTOMERS_NAME,
    MANIFEST_NAME,
    PRODUCTS_NAME,
    TRANSACTIONS_NAME,
)
from pipelines.raw import quote_literal  # Safe SQL string literals

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EvalBankLoadResult:
    """What the load wrote, and what it refused to: row counts per table (transactions counts
    only those inserted or updated) and the ids of the quarantined transactions."""

    rows: dict[str, int]
    quarantined: tuple[str, ...]


def _read_table(gold_dir: Path, name: str) -> tuple[tuple[str, ...], list[tuple[object, ...]]]:
    """Columns and rows of one eval-bank output, in the file's own column order.

    Raises
    ------
    FileNotFoundError
        When the eval bank has not been built.
    """
    path = gold_dir / name
    if not path.is_file():
        raise FileNotFoundError(f"eval bank output {name} not found; run the eval-bank build first")
    con = duckdb.connect()
    try:
        cursor = con.execute(f"SELECT * FROM read_parquet({quote_literal(str(path))})")
        columns = tuple(column[0] for column in cursor.description)
        return columns, cursor.fetchall()
    finally:
        con.close()


def _sha256(path: Path) -> str:
    """SHA-256 of a file, read in one pass."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _read_manifest(gold_dir: Path) -> dict[str, Any]:
    """The eval bank's manifest, parsed.

    Raises
    ------
    FileNotFoundError
        When the eval bank has not been built.
    ValueError
        When the manifest is not a JSON object.
    """
    path = gold_dir / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(
            f"eval bank manifest {MANIFEST_NAME} not found; run the eval-bank build first"
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"{path} is not a JSON object")
    return manifest


def _verify_output_digests(gold_dir: Path) -> None:
    """Refuse to load an output whose content no longer matches what its manifest recorded.

    Raises
    ------
    FileNotFoundError
        When the manifest or an output it describes is missing.
    ValueError
        When an output's digest disagrees with its manifest entry, or the manifest carries none.
    """
    manifest = _read_manifest(gold_dir)
    recorded = manifest.get("output_sha256")
    if not isinstance(recorded, dict):
        raise ValueError(f"{gold_dir / MANIFEST_NAME} carries no output_sha256")
    for name in (CUSTOMERS_NAME, PRODUCTS_NAME, TRANSACTIONS_NAME):
        path = gold_dir / name
        if not path.is_file():
            raise FileNotFoundError(
                f"eval bank output {name} not found; run the eval-bank build first"
            )
        expected = recorded.get(name)
        if not isinstance(expected, str) or not expected:
            raise ValueError(f"{gold_dir / MANIFEST_NAME} carries no digest for {name}")
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(
                f"{name} does not match its manifest digest "
                f"(expected {expected[:12]}, found {actual[:12]}); rebuild the eval bank"
            )


def _upsert(
    cur: psycopg.Cursor[Any],
    table: str,
    columns: tuple[str, ...],
    pk: str,
    data: list[tuple[object, ...]],
) -> None:
    """Insert ``data`` into ``table``, updating every non-key column on a primary-key conflict."""
    if not data:
        return
    column_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns if column != pk)
    cur.executemany(
        f"INSERT INTO {table} ({column_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({pk}) DO UPDATE SET {updates}",
        data,
    )


def load_eval_bank(dsn: str, gold_dir: Path) -> EvalBankLoadResult:
    """Load the eval bank additively into an already-migrated (and, for a full run, already
    seeded) serving store.

    Digests are verified and every Parquet file read before the database is touched. Customers
    and products are upserted first; each transaction is then checked against the store and
    quarantined (logged, counted, not written) when its customer or product does not resolve;
    the rest are upserted. Everything commits in one transaction.

    Returns
    -------
    EvalBankLoadResult
        Rows written per table and the ids of the quarantined transactions.

    Raises
    ------
    FileNotFoundError
        When the eval bank's Parquet output or manifest is missing.
    ValueError
        When the manifest is malformed, or an output no longer matches its manifest digest.
    psycopg.Error
        When the load itself fails; nothing already written commits.
    """
    _verify_output_digests(gold_dir)
    customer_columns, customer_rows = _read_table(gold_dir, CUSTOMERS_NAME)
    product_columns, product_rows = _read_table(gold_dir, PRODUCTS_NAME)
    transaction_columns, transaction_rows = _read_table(gold_dir, TRANSACTIONS_NAME)

    rows: dict[str, int] = {}
    quarantined: list[str] = []
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        _upsert(cur, "customers", customer_columns, "customer_id", customer_rows)
        rows["customers"] = len(customer_rows)
        _upsert(cur, "products", product_columns, "product_id", product_rows)
        rows["products"] = len(product_rows)

        customer_index = transaction_columns.index("customer_id")
        product_index = transaction_columns.index("product_id")
        id_index = transaction_columns.index("transaction_id")
        loadable: list[tuple[object, ...]] = []
        for row in transaction_rows:
            cur.execute(
                "SELECT (SELECT 1 FROM customers WHERE customer_id = %s), "
                "(SELECT 1 FROM products WHERE product_id = %s)",
                (row[customer_index], row[product_index]),
            )
            customer_found, product_found = cur.fetchone()  # type: ignore[misc]
            if customer_found is None or product_found is None:
                logger.warning(
                    "eval_bank_row_quarantined transaction_id=%s reason=orphan_fk",
                    row[id_index],
                )
                quarantined.append(str(row[id_index]))
                continue
            loadable.append(row)
        _upsert(cur, "transactions", transaction_columns, "transaction_id", loadable)
        rows["transactions"] = len(loadable)
        conn.commit()
    return EvalBankLoadResult(rows=rows, quarantined=tuple(quarantined))


def main(argv: Sequence[str] | None = None) -> int:
    """Load the eval bank from the command line.

    Returns
    -------
    int
        0 on success, whatever exit code an uncaught driver error would otherwise raise.
    """
    parser = argparse.ArgumentParser(
        description="Load the built evaluation bank into Postgres, additively."
    )
    parser.add_argument("--gold", type=Path, default=Path("data/gold/eval_bank"))
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: DATABASE_URL)")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        # An explicit --dsn does not need the rest of settings to be valid; only DATABASE_URL
        # resolution (below) does, and only when --dsn was not given.
        if not args.dsn:
            parser.error(str(exc))
        configure_logging_from_settings(None)
        dsn = args.dsn
    else:
        configure_logging_from_settings(settings)
        if args.dsn:
            dsn = args.dsn
        else:
            try:
                dsn = settings.require_database_url().get_secret_value()
            except ConfigError as exc:
                parser.error(str(exc))
    result = load_eval_bank(dsn, args.gold)
    logger.info(
        "eval_bank_loaded rows=%s quarantined=%s",
        ",".join(f"{table}:{count}" for table, count in sorted(result.rows.items())),
        ",".join(result.quarantined) if result.quarantined else "none",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
