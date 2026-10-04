"""
Postgres Spend Ledger Tests
============================

Component: ``app.persistence.spend_ledger``. Needs a real, migrated Postgres; marked
``integration``, skipped when ``DATABASE_URL`` is not set.
"""

from __future__ import annotations

# Standard libraries
import os
import threading
from datetime import date
from decimal import Decimal

# Third-party libraries
import psycopg
import pytest

# Local modules
from app.persistence.migrate import apply_migrations
from app.persistence.spend_ledger import PostgresSpendLedger

_DAY = date(2026, 6, 18)


@pytest.fixture
def ledger() -> PostgresSpendLedger:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE llm_spend_daily")
    return PostgresSpendLedger(dsn)


@pytest.mark.integration
def test_a_day_with_no_charge_reads_zero(ledger: PostgresSpendLedger) -> None:
    assert ledger.spent(_DAY) == Decimal(0)


@pytest.mark.integration
def test_charges_accumulate_per_day(ledger: PostgresSpendLedger) -> None:
    ledger.add(_DAY, Decimal("1.25"))
    ledger.add(_DAY, Decimal("0.5"))
    ledger.add(date(2026, 6, 19), Decimal("3"))

    assert ledger.spent(_DAY) == Decimal("1.75")
    assert ledger.spent(date(2026, 6, 19)) == Decimal("3")


@pytest.mark.integration
def test_concurrent_charges_all_land(ledger: PostgresSpendLedger) -> None:
    threads = [threading.Thread(target=ledger.add, args=(_DAY, Decimal("0.1"))) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert ledger.spent(_DAY) == Decimal("2.0")
