"""
Evaluation Bank Loader Tests
==============================

Component: ``app.persistence.load_eval_bank``. Reading the eval bank's own manifest and Parquet
files is hermetic. Loading them into the serving store for real needs a Postgres database,
already migrated: those tests are marked ``integration`` and read the DSN from ``DATABASE_URL``,
skipped when it is not set.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import duckdb
import psycopg
import pytest

from app.persistence import load_eval_bank as load_eval_bank_module
from app.persistence.load_eval_bank import (
    EvalBankLoadResult,
    _read_table,
    _verify_output_digests,
    load_eval_bank,
    main,
)
from app.persistence.load_seed import load_seed
from app.persistence.migrate import apply_migrations
from pipelines.eval_bank import (
    CUSTOMERS_NAME,
    MANIFEST_NAME,
    PRODUCTS_NAME,
    TRANSACTIONS_NAME,
    build_eval_bank,
)
from pipelines.ops_seed import CUSTOMERS_NAME as SEED_CUSTOMERS_NAME
from pipelines.ops_seed import MANIFEST_NAME as SEED_MANIFEST_NAME
from pipelines.ops_seed import PRODUCTS_NAME as SEED_PRODUCTS_NAME
from pipelines.ops_seed import TRANSACTIONS_NAME as SEED_TRANSACTIONS_NAME
from pipelines.raw import quote_literal


def test_main_requires_a_dsn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With no --dsn and no DATABASE_URL, the command refuses rather than guessing."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        main(["--gold", str(tmp_path)])


def test_main_uses_the_dsn_argument_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://env-only")
    seen: dict[str, object] = {}

    def fake_load_eval_bank(dsn: str, gold: Path) -> EvalBankLoadResult:
        seen["dsn"] = dsn
        return EvalBankLoadResult(rows={}, quarantined=())

    monkeypatch.setattr(load_eval_bank_module, "load_eval_bank", fake_load_eval_bank)

    exit_code = main(["--dsn", "postgresql://from-argument"])

    assert exit_code == 0
    assert seen["dsn"] == "postgresql://from-argument"


def test_read_table_raises_when_the_eval_bank_has_not_been_built(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="run the eval-bank build first"):
        _read_table(tmp_path, CUSTOMERS_NAME)


def _write_tiny_eval_bank_output(gold_dir: Path) -> None:
    """A gold directory with the three outputs and a manifest whose digests genuinely match —
    eval_bank's own manifest shape, which (unlike the seed's) carries no reference_date."""
    gold_dir.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    for name in (CUSTOMERS_NAME, PRODUCTS_NAME, TRANSACTIONS_NAME):
        path = gold_dir / name
        path.write_bytes(f"content of {name}".encode())
        digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (gold_dir / MANIFEST_NAME).write_text(json.dumps({"output_sha256": digests}), encoding="utf-8")


def test_verify_output_digests_passes_when_every_output_matches_its_manifest(
    tmp_path: Path,
) -> None:
    _write_tiny_eval_bank_output(tmp_path)
    _verify_output_digests(tmp_path)  # does not raise


def test_verify_output_digests_raises_when_an_output_no_longer_matches_its_manifest(
    tmp_path: Path,
) -> None:
    _write_tiny_eval_bank_output(tmp_path)
    (tmp_path / CUSTOMERS_NAME).write_bytes(b"tampered content")
    with pytest.raises(ValueError, match="does not match its manifest digest"):
        _verify_output_digests(tmp_path)


def test_load_eval_bank_refuses_a_tampered_output_before_ever_connecting(tmp_path: Path) -> None:
    _write_tiny_eval_bank_output(tmp_path)
    (tmp_path / TRANSACTIONS_NAME).write_bytes(b"tampered content")
    with pytest.raises(ValueError, match="does not match its manifest digest"):
        load_eval_bank("postgresql://unreachable.invalid/nowhere", tmp_path)


def test_load_eval_bank_raises_when_the_eval_bank_has_not_been_built(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="run the eval-bank build first"):
        load_eval_bank("postgresql://unreachable.invalid/nowhere", tmp_path)


@pytest.mark.integration
def test_load_eval_bank_loads_real_rows_and_quarantines_the_orphan_transaction(
    tmp_path: Path,
) -> None:
    """The whole point of this loader: the one authored eval_bank customer/product load, all
    three transactions that resolve to a real owner load, and the orphan transaction — whose
    customer_id and product_id resolve to nothing anywhere — is skipped rather than inserted with
    a relaxed foreign key."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE cases, transactions, products, customers CASCADE")
        conn.commit()

    gold = tmp_path / "eval_bank"
    build_eval_bank(gold, code_version="test")

    result = load_eval_bank(dsn, gold)

    assert result.rows == {"customers": 1, "products": 1, "transactions": 3}
    assert result.quarantined == ("TRX-EVALBANK-ORPHAN",)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM transactions")
        assert cur.fetchone() == (3,)
        cur.execute(
            "SELECT count(*) FROM transactions WHERE transaction_id = 'TRX-EVALBANK-ORPHAN'"
        )
        assert cur.fetchone() == (0,)
        cur.execute("SELECT customer_id FROM customers WHERE customer_id = 'CLI-EVALBANK-01'")
        assert cur.fetchone() == ("CLI-EVALBANK-01",)


@pytest.mark.integration
def test_load_eval_bank_never_creates_a_row_for_the_orphans_own_dangling_ids(
    tmp_path: Path,
) -> None:
    """Revert check for the quarantine itself: the orphan transaction's customer_id and
    product_id must never appear anywhere in the store — not as a placeholder customer/product,
    which would silently make the "no real owner" premise false."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE cases, transactions, products, customers CASCADE")
        conn.commit()

    gold = tmp_path / "eval_bank"
    build_eval_bank(gold, code_version="test")
    load_eval_bank(dsn, gold)

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM customers WHERE customer_id = 'CLI-EVALBANK-NONE'")
        assert cur.fetchone() == (0,)
        cur.execute("SELECT count(*) FROM products WHERE product_id = 'PRD-EVALBANK-NONE'")
        assert cur.fetchone() == (0,)


@pytest.mark.integration
def test_load_eval_bank_is_additive_alongside_an_already_loaded_seed(tmp_path: Path) -> None:
    """The core requirement this slice exists for: eval_bank loads on top of an already-loaded
    operational seed without truncating it, so both sources coexist in the store at once."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)

    # A tiny real-shaped ops_seed, loaded first via the ordinary (truncating) seed loader.
    seed_gold = tmp_path / "ops_seed"
    seed_gold.mkdir(parents=True)
    _write_seed_output(seed_gold)
    load_seed(dsn, seed_gold)

    eval_bank_gold = tmp_path / "eval_bank"
    build_eval_bank(eval_bank_gold, code_version="test")

    result = load_eval_bank(dsn, eval_bank_gold)

    assert result.rows == {"customers": 1, "products": 1, "transactions": 3}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # The seed's own customer is still there — eval_bank's load did not truncate it.
        cur.execute("SELECT count(*) FROM customers WHERE customer_id = 'CLI-SEED-1'")
        assert cur.fetchone() == (1,)
        # And eval_bank's own customer is there too, alongside it.
        cur.execute("SELECT count(*) FROM customers WHERE customer_id = 'CLI-EVALBANK-01'")
        assert cur.fetchone() == (1,)


@pytest.mark.integration
def test_load_eval_bank_is_idempotent(tmp_path: Path) -> None:
    """Re-running the loader upserts rather than duplicating or erroring on a primary-key
    conflict — a full evaluation run may load the eval bank more than once against the same
    database."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE cases, transactions, products, customers CASCADE")
        conn.commit()

    gold = tmp_path / "eval_bank"
    build_eval_bank(gold, code_version="test")

    first = load_eval_bank(dsn, gold)
    second = load_eval_bank(dsn, gold)

    assert first.rows == second.rows
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM customers WHERE customer_id = 'CLI-EVALBANK-01'")
        assert cur.fetchone() == (1,)


def _write_seed_output(gold_dir: Path) -> None:
    """A minimal, hand-written ops_seed-shaped gold directory, small enough not to need the
    real pipeline: one customer, one product, one transaction, digest-verified like the real
    thing."""
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE customers (customer_id VARCHAR, first_name VARCHAR, last_name VARCHAR, "
        "masked_email VARCHAR, masked_phone VARCHAR, country VARCHAR, customer_status VARCHAR)"
    )
    con.execute(
        "INSERT INTO customers VALUES ('CLI-SEED-1', 'A', 'B', NULL, NULL, 'México', 'Active')"
    )
    con.execute(
        "CREATE TABLE products (product_id VARCHAR, customer_id VARCHAR, product_type VARCHAR, "
        "last4 VARCHAR, product_status VARCHAR)"
    )
    con.execute(
        "INSERT INTO products VALUES ('PRD-SEED-1', 'CLI-SEED-1', 'Cuenta', '1234', 'Active')"
    )
    con.execute(
        "CREATE TABLE transactions (transaction_id VARCHAR, customer_id VARCHAR, "
        "product_id VARCHAR, transaction_date TIMESTAMP, transaction_type VARCHAR, "
        "merchant_name VARCHAR, amount DECIMAL(15,2), currency VARCHAR, amount_usd DOUBLE, "
        "amount_usd_provenance VARCHAR, transaction_status VARCHAR)"
    )
    con.execute(
        "INSERT INTO transactions VALUES ('TRX-SEED-1', 'CLI-SEED-1', 'PRD-SEED-1', "
        "'2026-06-18 12:00:00', 'Purchase', 'A Merchant', 20.0, 'USD', 20.0, 'reported', "
        "'Approved')"
    )
    digests: dict[str, str] = {}
    for name in (SEED_CUSTOMERS_NAME, SEED_PRODUCTS_NAME, SEED_TRANSACTIONS_NAME):
        table = name.removesuffix(".parquet")
        target = gold_dir / name
        con.execute(f"COPY {table} TO {quote_literal(str(target))} (FORMAT PARQUET)")
        digests[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    con.close()
    (gold_dir / SEED_MANIFEST_NAME).write_text(
        json.dumps({"reference_date": "2026-06-18", "output_sha256": digests}), encoding="utf-8"
    )
