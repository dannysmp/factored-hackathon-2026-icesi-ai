"""
Operational Metadata Reader
===========================

Overview
--------
Reads the loaded seed's own facts about itself (today, only ``data_as_of``, the reference date of
the loaded data) from the ``ops_meta`` table the seed's load job writes. A read that cannot
succeed answers ``None``, the same as a seed that was never loaded: the caller (the domain
calendar) decides what that means, this module only reports what it found.

Scope
-----
In: one read of one key from ``ops_meta``.
Out: writing ``ops_meta`` (the seed's load job), deciding what a missing value means
(``app.domain.calendar``).

Design Principles
-----------------
- Never raises for "the seed is not loaded yet": an unreachable database, a missing table and a
  missing row all answer ``None``, indistinguishable to the caller, which already treats "the seed
  has no date" as one case regardless of why.
- A short connection timeout, since this read happens at start-up, on a path that must fail fast
  rather than hang the whole service waiting on a database that is not there.

Runtime Contract
----------------
``read_data_as_of(dsn) -> date | None``.
"""

from __future__ import annotations

# Standard libraries
from datetime import date  # The seed's own reference date

# Third-party libraries
import psycopg  # Serving-store driver

_CONNECT_TIMEOUT_SECONDS = 5


def read_data_as_of(dsn: str) -> date | None:
    """The seed's own reference date, or ``None`` when it cannot be read.

    ``None`` covers an unreachable store, a missing table or ``data_as_of`` row, and a stored value
    that is not an ISO date.
    """
    try:
        with (
            psycopg.connect(dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute("SELECT value FROM ops_meta WHERE key = %s", ("data_as_of",))
            row = cur.fetchone()
    except psycopg.Error:
        return None
    if row is None:
        return None
    try:
        return date.fromisoformat(row[0])
    except ValueError:
        return None
