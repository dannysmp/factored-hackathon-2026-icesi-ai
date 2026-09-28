"""
Demo Persona Reset Tests
=========================

Component: ``app.persistence.reset_demo_personas``. Needs a real, migrated Postgres. Marked
``integration``, skipped when ``DATABASE_URL`` is not set.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from app.persistence.migrate import apply_migrations
from app.persistence.reset_demo_personas import reset_demo_personas
from app.security.demo_personas import CustomerPersona, PersonaList


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(value)
    with psycopg.connect(value) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute("TRUNCATE TABLE cases, transactions, products, customers CASCADE")
        conn.commit()
    with psycopg.connect(value, autocommit=True) as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO customers (customer_id, first_name, last_name, country, "
            "customer_status) VALUES (%s, 'First', 'Last', 'México', 'Active')",
            [("CUST-1",), ("CUST-2",)],
        )
        cur.executemany(
            "INSERT INTO products (product_id, customer_id, product_type, last4, "
            "product_status) VALUES (%s, %s, 'Cuenta Corriente', '1234', 'Active')",
            [("PRD-1", "CUST-1"), ("PRD-2", "CUST-2")],
        )
        cur.executemany(
            "INSERT INTO transactions (transaction_id, customer_id, product_id, "
            "transaction_date, transaction_type, merchant_name, amount, currency, amount_usd, "
            "amount_usd_provenance, transaction_status) VALUES "
            "(%s, %s, %s, '2026-06-08 09:00:00', 'Purchase', 'A Merchant', 100.00, 'USD', "
            "100.00, 'reported', 'Approved')",
            [("TRX-1", "CUST-1", "PRD-1"), ("TRX-2", "CUST-2", "PRD-2")],
        )
        cur.executemany(
            "INSERT INTO cases (case_number, customer_id, transaction_id, session_id, "
            "idempotency_key, status, category, amount, currency, amount_provenance, "
            "domain_date, expected_first_response_date, created_at_utc, policy_version, "
            "reason_code, language) VALUES "
            "(%s, %s, %s, 'SESSION-0', %s, 'Open', 'unrecognized_charge', 100.00, 'USD', "
            "'reported', '2026-06-01', '2026-10-01', '2026-06-01 09:00:00+00', '2', "
            "'eligible', 'en')",
            [
                ("CASE-1", "CUST-1", "TRX-1", "IDEMP-1"),
                ("CASE-2", "CUST-2", "TRX-2", "IDEMP-2"),
            ],
        )
    return value


def _persona_list(*customer_ids: str) -> PersonaList:
    return PersonaList(
        version=1,
        customers=tuple(
            CustomerPersona(
                slug=f"persona-{i}", customer_id=customer_id, language="es", scenario="eligible"
            )
            for i, customer_id in enumerate(customer_ids)
        ),
    )


@pytest.mark.integration
def test_a_personas_own_cases_are_deleted(dsn: str) -> None:
    deleted = reset_demo_personas(dsn, _persona_list("CUST-1"))

    assert deleted == 1
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT case_number FROM cases")
        rows = cur.fetchall()
    assert rows == [("CASE-2",)]


@pytest.mark.integration
def test_a_customer_not_in_the_persona_list_keeps_its_cases(dsn: str) -> None:
    reset_demo_personas(dsn, _persona_list("CUST-1"))

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM cases WHERE customer_id = 'CUST-2'")
        (count,) = cur.fetchone() or (0,)
    assert count == 1


@pytest.mark.integration
def test_both_personas_own_cases_are_deleted_together(dsn: str) -> None:
    deleted = reset_demo_personas(dsn, _persona_list("CUST-1", "CUST-2"))

    assert deleted == 2
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM cases")
        (count,) = cur.fetchone() or (0,)
    assert count == 0
