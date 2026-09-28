"""
CI Smoke Seed Tests
======================

Component: ``tests.fixtures.ci_smoke_seed``. The extraction helpers are hermetic. Actually
inserting and reading the rows back needs a real, migrated Postgres; marked ``integration``,
skipped when ``DATABASE_URL`` is not set.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from app.persistence.migrate import apply_migrations
from evals.runner.seed_resolution import resolve_customer_id
from evals.runner.smoke import smoke_cases
from tests.fixtures.ci_smoke_seed import (
    _foreign_transaction_ids,
    _needs_poisoned_merchant_row,
    _self_customer_ids,
    seed_ci_smoke_data,
)

# -----------------------------------------------------------------------------
# Extraction — hermetic
# -----------------------------------------------------------------------------


def test_self_customer_ids_covers_every_smoke_case_exactly_once() -> None:
    """Every one of the 16 smoke cases names its own session customer directly, ops_seed:CLI-...
    — the injection and unauthorized-access subtypes both do, the poisoned-merchant subtype
    (eval_bank:TRX-...) does not."""
    ids = _self_customer_ids(smoke_cases())

    assert len(ids) == 12
    assert len(set(ids)) == 12
    assert all(customer_id.startswith("CLI-") for customer_id in ids)


def test_foreign_transaction_ids_finds_exactly_the_six_unauthorized_access_references() -> None:
    ids = _foreign_transaction_ids(smoke_cases())

    assert len(ids) == 6
    assert all(transaction_id.startswith("TRX-") for transaction_id in ids)
    # None of the self customers' own derived ids collide with a foreign one.
    assert set(ids).isdisjoint(_self_customer_ids(smoke_cases()))


def test_needs_poisoned_merchant_row_is_true_for_the_real_smoke_set() -> None:
    assert _needs_poisoned_merchant_row(smoke_cases()) is True


# -----------------------------------------------------------------------------
# Seeding — needs the real store
# -----------------------------------------------------------------------------


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(value)
    with psycopg.connect(value) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute(
            "TRUNCATE TABLE cases, transactions, products, customers, audit_log, "
            "handoff_outbox CASCADE"
        )
        conn.commit()
    return value


@pytest.mark.integration
def test_seed_ci_smoke_data_resolves_every_self_customer(dsn: str) -> None:
    seed_ci_smoke_data(dsn)

    for case in smoke_cases():
        source, identifier = case.seed_ref.split(":", 1)
        if source == "ops_seed" and identifier.startswith("CLI-"):
            assert resolve_customer_id(dsn, case.seed_ref) == identifier


@pytest.mark.integration
def test_seed_ci_smoke_data_owns_every_foreign_transaction_by_someone_else(dsn: str) -> None:
    seed_ci_smoke_data(dsn)
    self_ids = set(_self_customer_ids(smoke_cases()))

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for transaction_id in _foreign_transaction_ids(smoke_cases()):
            cur.execute(
                "SELECT customer_id FROM transactions WHERE transaction_id = %s", (transaction_id,)
            )
            row = cur.fetchone()
            assert row is not None, f"{transaction_id} was not seeded"
            assert row[0] not in self_ids


@pytest.mark.integration
def test_seed_ci_smoke_data_seeds_the_poisoned_merchant_row_the_real_scenario_needs(
    dsn: str,
) -> None:
    seed_ci_smoke_data(dsn)

    assert resolve_customer_id(dsn, "eval_bank:TRX-EVALBANK-POISONED-MERCHANT") == "CLI-EVALBANK-01"


@pytest.mark.integration
def test_seed_ci_smoke_data_is_idempotent(dsn: str) -> None:
    seed_ci_smoke_data(dsn)
    seed_ci_smoke_data(dsn)  # must not raise a duplicate-key error

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM customers")
        row = cur.fetchone()
    assert row is not None
    assert row[0] > 0
