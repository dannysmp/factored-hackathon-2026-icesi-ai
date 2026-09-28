"""
Demo Persona Reset Tests
=========================

Component: ``app.persistence.reset_demo_personas``. The command-line wiring is pure and hermetic.
Deleting rows for real needs a Postgres database: those tests are marked ``integration`` and read
the DSN from ``DATABASE_URL``, skipped when it is not set.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from app.persistence import reset_demo_personas as reset_demo_personas_module
from app.persistence.migrate import apply_migrations
from app.persistence.reset_demo_personas import main, reset_demo_personas
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
                slug=f"persona-{i}",
                display_name=f"Persona {i}",
                customer_id=customer_id,
                language="es",
                scenario="eligible",
            )
            for i, customer_id in enumerate(customer_ids)
        ),
    )


def test_main_requires_a_dsn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With no --dsn and no DATABASE_URL, the command refuses rather than guessing.

    Run from an empty directory: ``main`` loads settings with the default ``.env`` path, so a
    real ``.env`` in the working directory must not supply a DSN this test means to be absent.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        main([])


def test_main_uses_the_dsn_argument_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit --dsn is used even when DATABASE_URL is also set."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://env-only")
    monkeypatch.setattr(
        reset_demo_personas_module, "load_personas", lambda: _persona_list("CUST-1")
    )
    seen: dict[str, object] = {}

    def fake_reset(dsn: str, personas: PersonaList) -> int:
        seen["dsn"] = dsn
        seen["personas"] = personas
        return 3

    monkeypatch.setattr(reset_demo_personas_module, "reset_demo_personas", fake_reset)

    exit_code = main(["--dsn", "postgresql://from-argument"])

    assert exit_code == 0
    assert seen["dsn"] == "postgresql://from-argument"


def test_main_refuses_when_settings_are_invalid_and_no_dsn_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without --dsn, a fully invalid settings load is refused too, not just a missing DSN."""
    monkeypatch.setenv("SESSION_SIGNING_KEY", "too-short")

    with pytest.raises(SystemExit):
        main([])


def test_main_uses_the_dsn_argument_even_with_an_unrelated_invalid_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit --dsn must not be blocked by a setting that DSN resolution never reads."""
    monkeypatch.setenv("SESSION_SIGNING_KEY", "too-short")
    monkeypatch.setattr(
        reset_demo_personas_module, "load_personas", lambda: _persona_list("CUST-1")
    )
    seen: dict[str, object] = {}

    def fake_reset(dsn: str, personas: PersonaList) -> int:
        seen["dsn"] = dsn
        return 3

    monkeypatch.setattr(reset_demo_personas_module, "reset_demo_personas", fake_reset)

    exit_code = main(["--dsn", "postgresql://from-argument"])

    assert exit_code == 0
    assert seen["dsn"] == "postgresql://from-argument"


def test_main_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL is used when --dsn is not given."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://env-only")
    monkeypatch.setattr(
        reset_demo_personas_module, "load_personas", lambda: _persona_list("CUST-1")
    )
    seen: dict[str, object] = {}

    def fake_reset(dsn: str, personas: PersonaList) -> int:
        seen["dsn"] = dsn
        return 0

    monkeypatch.setattr(reset_demo_personas_module, "reset_demo_personas", fake_reset)

    exit_code = main([])

    assert exit_code == 0
    assert seen["dsn"] == "postgresql://env-only"


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
