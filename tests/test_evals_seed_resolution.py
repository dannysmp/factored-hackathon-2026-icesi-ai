"""
Seed Reference Resolution Tests
=================================

Component: ``evals.runner.seed_resolution``. ``parse_seed_ref`` is hermetic. ``resolve_customer_id``
needs a real, migrated Postgres for its transaction-lookup path; marked ``integration``, skipped
when ``DATABASE_URL`` is not set, matching ``tests.test_persistence_reads``'s own convention.
"""

from __future__ import annotations

# Standard libraries
import os

# Third-party libraries
import psycopg
import pytest

# Local modules
from app.persistence.migrate import apply_migrations
from evals.runner.seed_resolution import parse_seed_ref, resolve_customer_id

# -----------------------------------------------------------------------------
# parse_seed_ref — hermetic
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seed_ref", "source", "identifier"),
    [
        ("ops_seed:CLI-A1B2", "ops_seed", "CLI-A1B2"),
        ("ops_seed:TRX-C3D4", "ops_seed", "TRX-C3D4"),
        ("eval_bank:TRX-EVALBANK-ORPHAN", "eval_bank", "TRX-EVALBANK-ORPHAN"),
    ],
)
def test_parse_seed_ref_splits_source_and_identifier(
    seed_ref: str, source: str, identifier: str
) -> None:
    assert parse_seed_ref(seed_ref) == (source, identifier)


@pytest.mark.parametrize(
    "seed_ref",
    [
        "CLI-A1B2",  # no source at all
        "unknown_source:CLI-A1B2",  # source outside the closed set
        "ops_seed:",  # empty identifier
        "",
    ],
)
def test_parse_seed_ref_rejects_a_malformed_reference(seed_ref: str) -> None:
    with pytest.raises(ValueError, match="malformed seed_ref"):
        parse_seed_ref(seed_ref)


# -----------------------------------------------------------------------------
# resolve_customer_id — needs the real store for the transaction-lookup path
# -----------------------------------------------------------------------------


def test_resolve_customer_id_returns_a_customer_reference_directly() -> None:
    """No store access at all for a customer-shaped identifier; safe to run with no DSN."""
    assert resolve_customer_id("postgresql://unused", "ops_seed:CLI-A1B2") == "CLI-A1B2"


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(value)
    with psycopg.connect(value) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute("TRUNCATE TABLE cases, transactions, products, customers, audit_log CASCADE")
        conn.commit()
    with psycopg.connect(value, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customers (customer_id, first_name, last_name, country, "
            "customer_status) VALUES ('CLI-A', 'First', 'Last', 'México', 'Active')"
        )
        cur.execute(
            "INSERT INTO products (product_id, customer_id, product_type, last4, "
            "product_status) VALUES ('PRD-A', 'CLI-A', 'Cuenta Corriente', '1234', 'Active')"
        )
        cur.execute(
            "INSERT INTO transactions (transaction_id, customer_id, product_id, "
            "transaction_date, transaction_type, merchant_name, amount, currency, amount_usd, "
            "amount_usd_provenance, transaction_status) VALUES "
            "('TRX-A1', 'CLI-A', 'PRD-A', '2026-06-08 09:00:00', 'Purchase', 'A Merchant', "
            "100.00, 'USD', 100.00, 'reported', 'Approved')"
        )
    return value


@pytest.mark.integration
def test_resolve_customer_id_looks_up_the_transactions_owning_customer(dsn: str) -> None:
    assert resolve_customer_id(dsn, "ops_seed:TRX-A1") == "CLI-A"


@pytest.mark.integration
def test_resolve_customer_id_raises_for_a_transaction_absent_from_the_store(dsn: str) -> None:
    with pytest.raises(ValueError, match="names no transaction"):
        resolve_customer_id(dsn, "ops_seed:TRX-DOES-NOT-EXIST")


@pytest.mark.integration
def test_resolve_customer_id_looks_up_an_eval_bank_reference_the_same_way(dsn: str) -> None:
    """An eval_bank-sourced transaction id resolves through the exact same query as an
    ops_seed one, once its row exists in the same transactions table — eval_bank's own design
    principle: a reader treats its rows exactly like seed rows."""
    assert resolve_customer_id(dsn, "eval_bank:TRX-A1") == "CLI-A"


def test_resolve_customer_id_rejects_an_identifier_shaped_like_neither() -> None:
    with pytest.raises(ValueError, match="names neither"):
        resolve_customer_id("postgresql://unused", "ops_seed:PRD-A1B2")
