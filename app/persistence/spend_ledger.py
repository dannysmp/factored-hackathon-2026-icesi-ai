"""
Postgres Spend Ledger
=====================

Overview
--------
The day's model spend, kept in the ``llm_spend_daily`` table so it survives a restart and is
shared by every process that talks to the model.

Scope
-----
In: reading a day's total and adding to it atomically.
Out: deciding when the total is too high (``app.llm.spend``).

Design Principles
-----------------
- One statement per charge (``INSERT … ON CONFLICT DO UPDATE``), so concurrent charges add up
  instead of overwriting each other.
- Connections follow the audit sink's pattern: opened for the call and closed with it, stateless.

Runtime Contract
----------------
``PostgresSpendLedger(dsn)`` implements ``app.llm.spend.SpendLedger``; both methods raise
``psycopg.Error`` when the database cannot be reached.
"""

from __future__ import annotations

# Standard libraries
from datetime import date  # The operating day
from decimal import Decimal  # Money is never a float

# Third-party libraries
import psycopg  # Postgres driver

_CONNECT_TIMEOUT_SECONDS = 5

_SELECT_SQL = "SELECT spent_usd FROM llm_spend_daily WHERE spend_day = %s"
_CHARGE_SQL = (
    "INSERT INTO llm_spend_daily (spend_day, spent_usd) VALUES (%s, %s) "
    "ON CONFLICT (spend_day) DO UPDATE "
    "SET spent_usd = llm_spend_daily.spent_usd + EXCLUDED.spent_usd"
)


class PostgresSpendLedger:
    """A ``SpendLedger`` over ``llm_spend_daily``, one row per operating day."""

    def __init__(self, dsn: str) -> None:
        """Remember the connection string; no connection is opened until a method is called."""
        self._dsn = dsn

    def spent(self, day: date) -> Decimal:
        """Return the total charged for ``day``, or zero when nothing was charged that day."""
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(_SELECT_SQL, (day,))
            row = cur.fetchone()
        return Decimal(0) if row is None else Decimal(row[0])

    def add(self, day: date, usd: Decimal) -> None:
        """Add ``usd`` to the total for ``day`` in one atomic statement and commit it.

        The first charge of a day creates its row; later charges increase it in the database, so
        concurrent callers never overwrite each other's amounts.
        """
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(_CHARGE_SQL, (day, usd))
            conn.commit()
