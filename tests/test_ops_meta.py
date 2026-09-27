"""
Operational Metadata Reader Tests
====================================

Component: ``app.persistence.ops_meta``. The unreachable-database path is pure and hermetic
(a bogus DSN that fails to resolve, no real network needed). Reading a real ``ops_meta`` row is
marked ``integration`` and needs ``DATABASE_URL``, skipped when it is not set.
"""

from __future__ import annotations

import os
from datetime import date

import psycopg
import pytest

from app.persistence.ops_meta import read_data_as_of


def test_an_unreachable_database_answers_none_not_an_exception() -> None:
    """A start-up read must never crash the service; it is one more source that did not answer."""
    result = read_data_as_of("postgresql://nonexistent-host-for-this-test.invalid/db")

    assert result is None


@pytest.mark.integration
def test_reads_the_seeds_own_reference_date() -> None:
    """A genuine ops_meta row round-trips through the reader exactly."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS ops_meta (key VARCHAR(50) PRIMARY KEY, value TEXT NOT NULL)"
        )
        cur.execute(
            "INSERT INTO ops_meta (key, value) VALUES ('data_as_of', '2026-06-18') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )

    assert read_data_as_of(dsn) == date(2026, 6, 18)


@pytest.mark.integration
def test_answers_none_when_the_seed_has_no_reference_date() -> None:
    """A reachable database with no data_as_of row is the same as no seed: None, not an error."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS ops_meta (key VARCHAR(50) PRIMARY KEY, value TEXT NOT NULL)"
        )
        cur.execute("DELETE FROM ops_meta WHERE key = 'data_as_of'")

    assert read_data_as_of(dsn) is None
