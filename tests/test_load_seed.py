"""
Seed Loader Tests
=================

Component: ``app.persistence.load_seed``. Reading the seed's own manifest and Parquet files is
hermetic. Loading them into the serving store for real needs a Postgres database, already
migrated: those tests are marked ``integration`` and read the DSN from ``DATABASE_URL``, skipped
when it is not set.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import psycopg
import pytest

from app.persistence.load_seed import LoadResult, _read_reference_date, _read_table, load_seed
from app.persistence.migrate import apply_migrations
from pipelines import ops_seed
from pipelines.ops_seed import CUSTOMERS_NAME, MANIFEST_NAME


def test_read_table_raises_when_the_seed_has_not_been_built(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="build the seed first"):
        _read_table(tmp_path, CUSTOMERS_NAME)


def test_read_reference_date_raises_when_the_manifest_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="build the seed first"):
        _read_reference_date(tmp_path)


def test_read_reference_date_raises_when_the_manifest_carries_none(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_text(json.dumps({"code_version": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="carries no reference_date"):
        _read_reference_date(tmp_path)


def test_read_reference_date_reads_the_manifest(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_text(
        json.dumps({"reference_date": "2026-06-18"}), encoding="utf-8"
    )
    assert _read_reference_date(tmp_path) == "2026-06-18"


def _write_tiny_silver(root: Path) -> Path:
    """The smallest cleaned layer ``pipelines.ops_seed`` can build from: two Active customers."""
    silver = root / "silver"
    (silver / "silver").mkdir(parents=True)
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE customers (customer_id VARCHAR, first_name VARCHAR, last_name VARCHAR, "
        "email VARCHAR, mobile_phone VARCHAR, landline_phone VARCHAR, country VARCHAR, "
        "segment VARCHAR, customer_status VARCHAR)"
    )
    con.execute(
        "CREATE TABLE products (product_id VARCHAR, customer_id VARCHAR, product_type VARCHAR, "
        "product_number VARCHAR, product_status VARCHAR)"
    )
    con.execute(
        "CREATE TABLE transactions (transaction_id VARCHAR, customer_id VARCHAR, "
        "product_id VARCHAR, transaction_date TIMESTAMP, transaction_type VARCHAR, "
        "merchant_name VARCHAR, merchant_category VARCHAR, amount DECIMAL(15,2), "
        "currency VARCHAR, amount_usd DECIMAL(15,2), transaction_status VARCHAR)"
    )
    con.execute(
        "CREATE TABLE complaints (complaint_id VARCHAR, customer_id VARCHAR, "
        "creation_date TIMESTAMP, status VARCHAR, is_repeat_complainer BOOLEAN)"
    )
    con.execute(
        "CREATE TABLE daily_exchange_rates (date DATE, source_currency VARCHAR, "
        "target_currency VARCHAR, exchange_rate DECIMAL(12,6))"
    )
    con.executemany(
        "INSERT INTO customers VALUES (?, 'A', 'B', ?, '+1 555 0000', NULL, 'México', "
        "'Basic', 'Active')",
        [("CLI-1", "a@example.com"), ("CLI-2", "b@example.com")],
    )
    con.executemany(
        "INSERT INTO products VALUES (?, ?, 'Cuenta Corriente', '1234567890', 'Active')",
        [("PRD-1", "CLI-1"), ("PRD-2", "CLI-2")],
    )
    con.executemany(
        "INSERT INTO transactions VALUES (?, ?, ?, '2026-06-18 12:00:00', 'Purchase', "
        "'A Merchant', 'Food', 20.0, 'USD', 20.0, 'Approved')",
        [("TRX-1", "CLI-1", "PRD-1"), ("TRX-2", "CLI-2", "PRD-2")],
    )
    for name in ("customers", "products", "transactions", "complaints", "daily_exchange_rates"):
        target = silver / "silver" / f"{name}.parquet"
        con.execute(f"COPY {name} TO '{target}' (FORMAT PARQUET)")
    con.close()
    return silver


@pytest.mark.integration
def test_load_seed_loads_rows_and_records_the_reference_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)

    monkeypatch.setattr(ops_seed, "TARGET_CUSTOMERS", 2)
    monkeypatch.setattr(ops_seed, "MIN_PER_STRATUM", 1)
    silver = _write_tiny_silver(tmp_path / "base")
    gold = tmp_path / "gold"
    ops_seed.build_seed(silver, gold, code_version="test")

    result = load_seed(dsn, gold)

    assert result == LoadResult(
        rows={"customers": 2, "products": 2, "transactions": 2}, data_as_of="2026-06-18"
    )
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM customers")
        assert cur.fetchone() == (2,)
        cur.execute("SELECT value FROM ops_meta WHERE key = 'data_as_of'")
        assert cur.fetchone() == ("2026-06-18",)

    # Re-running truncates and reloads rather than duplicating or conflicting.
    again = load_seed(dsn, gold)
    assert again.rows == result.rows
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM customers")
        assert cur.fetchone() == (2,)


@pytest.mark.integration
def test_load_seed_raises_when_the_seed_has_not_been_built(tmp_path: Path) -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)

    with pytest.raises(FileNotFoundError):
        load_seed(dsn, tmp_path / "no-such-seed")
