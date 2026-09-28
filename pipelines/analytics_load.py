"""
Analytics Mart Load
====================

Overview
--------
Loads the dispute-demand gold marts (``pipelines.gold``) into the Postgres ``analytics`` schema
(ADR-11, migration ``0004_analytics_schema``) that the BI dashboard reads, instead of DuckDB or
Parquet directly.

Scope
-----
In: reading ``pipelines.gold``'s marts and its manifest, truncating and reloading every
``analytics.<mart>`` table in one transaction, verifying row-count and content-checksum parity
between the gold marts and what Postgres now holds, the command line
``python -m pipelines.analytics_load``.
Out: building the marts themselves (``pipelines.gold``), creating the schema, tables and the
read-only role (``app.persistence.migrate``, which must already have run), Metabase's own
provisioning (a later slice).

Design Principles
------------------
- One transaction: the truncate, every insert and the parity check happen together, so a failed
  or disagreeing load never leaves the dashboard on a half-updated or silently wrong mix of marts
  — the transaction is rolled back by never being committed.
- Verifies every mart's Parquet output against ``pipelines.gold``'s own manifest digest before
  loading anything, the same rule ``app.persistence.load_seed`` follows for the operational seed.
- **Parity is measured, not assumed** (ADR-11's hardening line: "a per-mart row-count and
  checksum parity test compares the Postgres copy with the lake"). After each mart loads, its row
  count and a content checksum are compared between the rows read from the gold Parquet file and
  the same columns read back from Postgres. The checksum is computed over the **sorted** set of
  canonicalized rows, not a fixed row order: Postgres makes no ordering guarantee for a plain
  ``SELECT`` and this check must hold regardless of physical row order.
- Canonicalizes ``date`` and ``Decimal`` values to strings before hashing, so the same logical
  value hashes identically whether DuckDB or psycopg produced the Python object.
- Column names and table names are interpolated directly into SQL (`ruff`'s ``S608`` is disabled
  for this module in ``pyproject.toml``): both come only from ``pipelines.gold``'s own fixed
  ``MART_NAMES`` and the gold marts' own Parquet headers, never from a request.

Runtime Contract
----------------
``load_marts(dsn, gold_dir) -> LoadResult``
The command line ``python -m pipelines.analytics_load``.

Limitations
-----------
Assumes migration ``0004_analytics_schema`` has already run; a store without the ``analytics``
schema and its tables fails with the driver's own error, not a friendlier one. The
``analytics_reader`` role that migration creates has no password until Metabase's own
provisioning slice sets one — this job connects with the same credential the serving store's
other maintenance jobs use, never as ``analytics_reader`` itself.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command line
import hashlib  # Digests for the manifest check and the parity checksum
import json  # Reading the gold manifest; canonicalizing rows for the checksum
import logging  # Progress events, never print
import sys  # Log stream
from collections.abc import Sequence  # Types of parsed argv and mart rows
from dataclasses import dataclass  # Immutable result objects
from datetime import date  # Canonicalized to isoformat before hashing
from decimal import Decimal  # Canonicalized to its exact string before hashing
from pathlib import Path  # Locations of the gold marts
from typing import Any  # The parsed manifest

# Third-party libraries
import duckdb  # Reads the gold marts' own Parquet files
import psycopg  # Serving-store driver

# Local modules
from app.config import ConfigError, load_settings  # The one validated source of DATABASE_URL
from pipelines.gold import MANIFEST_NAME, MART_NAMES  # The marts' fixed names and manifest name
from pipelines.raw import quote_literal  # Safe SQL string literals

logger = logging.getLogger(__name__)

DEFAULT_GOLD = Path("data/gold/dispute_demand")


@dataclass(frozen=True, slots=True)
class MartParity:
    """One mart's row count and content checksum, verified against the gold mart after the load."""

    rows: int
    checksum: str


@dataclass(frozen=True, slots=True)
class LoadResult:
    """What the load wrote and verified, per mart."""

    parity: dict[str, MartParity]


def _read_mart(gold_dir: Path, name: str) -> tuple[tuple[str, ...], list[tuple[object, ...]]]:
    """Columns and rows of one gold mart, in the file's own column order.

    Raises
    ------
    FileNotFoundError
        When the mart has not been built.
    """
    path = gold_dir / f"{name}.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"mart {name} not found; run the gold build first")
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
    """The gold build's manifest, parsed.

    Raises
    ------
    FileNotFoundError
        When the marts have not been built.
    ValueError
        When the manifest is not a JSON object.
    """
    path = gold_dir / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(
            f"gold manifest {MANIFEST_NAME} not found; run the gold build first"
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"{path} is not a JSON object")
    return manifest


def _verify_output_digests(gold_dir: Path) -> None:
    """Refuse to load a mart whose content no longer matches the gold build's own manifest.

    Raises
    ------
    FileNotFoundError
        When the manifest or a mart it describes is missing.
    ValueError
        When the manifest carries no digest for a mart, or a mart's digest disagrees with it:
        the gold directory changed since the marts were built, and this load would not be
        loading what the manifest describes.
    """
    manifest = _read_manifest(gold_dir)
    marts = manifest.get("marts")
    if not isinstance(marts, dict):
        raise ValueError(f"{gold_dir / MANIFEST_NAME} carries no marts entry")
    for name in MART_NAMES:
        path = gold_dir / f"{name}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"mart {name} not found; run the gold build first")
        entry = marts.get(name)
        expected = entry.get("sha256") if isinstance(entry, dict) else None
        if not isinstance(expected, str) or not expected:
            raise ValueError(f"{gold_dir / MANIFEST_NAME} carries no digest for {name}")
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(
                f"{name} does not match its manifest digest "
                f"(expected {expected[:12]}, found {actual[:12]}); rebuild the marts"
            )


def _canonical(value: object) -> object:
    """`value` in the JSON-serialisable form that DuckDB and psycopg agree on.

    Both drivers already return the same native Python type for the same logical value (`date`,
    `Decimal`, `int`, `float`, `str` or `None`); only `date` and `Decimal` are not natively
    JSON-serialisable, so those two convert to their exact string form and everything else passes
    through unchanged.
    """
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _row_checksum(rows: Sequence[tuple[object, ...]]) -> str:
    """Order-independent content digest of `rows`.

    A plain ``SELECT`` gives Postgres no ordering guarantee, so the digest sorts the rows'
    canonical JSON forms first: the same multiset of rows always hashes the same, regardless of
    which physical order the reading query happened to return.
    """
    lines = sorted(json.dumps([_canonical(value) for value in row]) for row in rows)
    hasher = hashlib.sha256()
    for line in lines:
        hasher.update(line.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def load_marts(dsn: str, gold_dir: Path) -> LoadResult:
    """Truncate and reload every dispute-demand mart into the ``analytics`` schema, in one
    transaction, then verify row-count and content-checksum parity against the gold marts.

    Raises
    ------
    FileNotFoundError
        When a mart's Parquet output or the gold manifest is missing.
    ValueError
        When the manifest is malformed, a mart no longer matches its manifest digest, or the
        loaded row count or content checksum disagrees with the gold mart it was loaded from.
    psycopg.Error
        When the load itself fails; nothing already written commits.
    """
    _verify_output_digests(gold_dir)
    marts = [(name, *_read_mart(gold_dir, name)) for name in MART_NAMES]

    parity: dict[str, MartParity] = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE " + ", ".join(f"analytics.{name}" for name in MART_NAMES))
        for name, columns, rows in marts:
            column_list = ", ".join(columns)
            placeholders = ", ".join(["%s"] * len(columns))
            if rows:
                cur.executemany(
                    f"INSERT INTO analytics.{name} ({column_list}) VALUES ({placeholders})", rows
                )
            cur.execute(f"SELECT {column_list} FROM analytics.{name}")
            loaded_rows = cur.fetchall()
            if len(loaded_rows) != len(rows):
                raise ValueError(
                    f"{name}: Postgres holds {len(loaded_rows)} row(s), the gold mart has "
                    f"{len(rows)}"
                )
            source_checksum = _row_checksum(rows)
            loaded_checksum = _row_checksum(loaded_rows)
            if loaded_checksum != source_checksum:
                raise ValueError(
                    f"{name}: Postgres content checksum {loaded_checksum[:12]} does not match "
                    f"the gold mart's {source_checksum[:12]}"
                )
            parity[name] = MartParity(rows=len(rows), checksum=source_checksum)
        conn.commit()
    return LoadResult(parity=parity)


def main(argv: Sequence[str] | None = None) -> int:
    """Load the analytics marts from the command line.

    Returns
    -------
    int
        0 on success, whatever exit code an uncaught driver error would otherwise raise.
    """
    parser = argparse.ArgumentParser(
        description="Load the dispute-demand gold marts into the Postgres analytics schema."
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
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
    result = load_marts(dsn, args.gold)
    logger.info(
        "analytics_marts_loaded rows=%s",
        ",".join(f"{name}:{p.rows}" for name, p in sorted(result.parity.items())),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
