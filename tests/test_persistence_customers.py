"""
Customer Lookup Tests
=====================

Component: ``app.persistence.customers``. Needs a real, migrated Postgres for the found/not-found
cases; marked ``integration``, skipped when ``DATABASE_URL`` is not set. The unreachable-store
case needs no real database and runs in the fast suite.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from app.persistence.customers import customer_status
from app.persistence.migrate import apply_migrations


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(value)
    with psycopg.connect(value, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE cases, transactions, products, customers CASCADE")
        cur.executemany(
            "INSERT INTO customers (customer_id, first_name, last_name, country, "
            "customer_status) VALUES (%s, 'A', 'B', 'México', %s)",
            [("CLI-ACTIVE", "Active"), ("CLI-SUSPENDED", "Suspended")],
        )
    return value


@pytest.mark.integration
def test_an_active_customer_is_found(dsn: str) -> None:
    assert customer_status(dsn, "CLI-ACTIVE") == "Active"


@pytest.mark.integration
def test_a_suspended_customer_is_still_found_with_its_real_status(dsn: str) -> None:
    """No status gate here: the status is reported as it is, not filtered."""
    assert customer_status(dsn, "CLI-SUSPENDED") == "Suspended"


@pytest.mark.integration
def test_an_unknown_customer_is_not_found(dsn: str) -> None:
    assert customer_status(dsn, "CLI-NOBODY") is None


def test_an_unreachable_store_is_reported_as_not_found() -> None:
    """Indistinguishable from "no match": the sandbox login handles both the same way."""
    assert customer_status("postgresql://unreachable.invalid/nowhere", "CLI-1") is None
