"""
Serving-Store Migration Tests
===============================

Component: ``app.persistence.migrate``. The checksum and command-line wiring are pure and
hermetic. Applying a migration for real needs a Postgres database: those tests are marked
``integration`` and read the DSN from ``DATABASE_URL``, skipped when it is not set.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from app.persistence import migrate as migrate_module
from app.persistence.migrate import MIGRATIONS_DIR, _checksum, apply_migrations, main


def test_checksum_is_deterministic() -> None:
    """The same file content always yields the same checksum."""
    assert _checksum("create table x ();") == _checksum("create table x ();")


def test_checksum_differs_for_different_content() -> None:
    """Two different migration bodies never collide in the common case."""
    assert _checksum("create table x ();") != _checksum("create table y ();")


def test_main_requires_a_dsn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With no --dsn and no DATABASE_URL, the command refuses rather than guessing.

    Run from an empty directory: ``main`` loads settings with the default ``.env`` path, so a
    real ``.env`` in the working directory (exactly what a developer has after following this
    project's own setup) must not supply a DSN this test means to be absent.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        main([])


def test_main_uses_the_dsn_argument_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit --dsn is used even when DATABASE_URL is also set."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://env-only")
    seen: dict[str, str] = {}

    def fake_apply(dsn: str, **_kwargs: object) -> tuple[str, ...]:
        seen["dsn"] = dsn
        return ()

    monkeypatch.setattr(migrate_module, "apply_migrations", fake_apply)

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
    """An explicit --dsn must not be blocked by a setting that DSN resolution never reads.

    A malformed SESSION_SIGNING_KEY fails full settings validation; --dsn does not need full
    settings to be valid, only DATABASE_URL resolution does, and that is skipped when --dsn is
    given.
    """
    monkeypatch.setenv("SESSION_SIGNING_KEY", "too-short")
    seen: dict[str, str] = {}

    def fake_apply(dsn: str, **_kwargs: object) -> tuple[str, ...]:
        seen["dsn"] = dsn
        return ()

    monkeypatch.setattr(migrate_module, "apply_migrations", fake_apply)

    exit_code = main(["--dsn", "postgresql://from-argument"])

    assert exit_code == 0
    assert seen["dsn"] == "postgresql://from-argument"


def test_main_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL is used when --dsn is not given."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://env-only")
    seen: dict[str, str] = {}

    def fake_apply(dsn: str, **_kwargs: object) -> tuple[str, ...]:
        seen["dsn"] = dsn
        return ("0001_serving_store",)

    monkeypatch.setattr(migrate_module, "apply_migrations", fake_apply)

    exit_code = main([])

    assert exit_code == 0
    assert seen["dsn"] == "postgresql://env-only"


def test_migration_0001_is_present_and_nonempty() -> None:
    """The frozen first migration exists where the runner looks for it."""
    path = MIGRATIONS_DIR / "0001_serving_store.sql"

    assert path.is_file()
    assert path.read_text(encoding="utf-8").strip()


@pytest.mark.integration
def test_migrations_apply_cleanly_to_a_fresh_database() -> None:
    """Every migration file applies without error against a real, empty Postgres."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "DROP TABLE IF EXISTS cases, transactions, products, customers, "
            "ops_meta, audit_log, signin_audit, handoff_actions, handoff_open_questions, "
            "handoff_reason_codes, handoff_sources, handoff_outbox, dialogue_state, "
            "dialogue_turn_log, schema_migrations CASCADE"
        )
        # A table's own trigger drops with it, but the function it calls is a separate object.
        cur.execute("DROP FUNCTION IF EXISTS audit_log_forbid_mutation() CASCADE")
        cur.execute("DROP FUNCTION IF EXISTS dialogue_turn_log_forbid_mutation() CASCADE")
        # The analytics schema's tables drop with it; the role does not and is cluster-global.
        cur.execute("DROP SCHEMA IF EXISTS analytics CASCADE")
        cur.execute("DROP ROLE IF EXISTS analytics_reader")

    applied = apply_migrations(dsn)
    assert applied == (
        "0001_serving_store",
        "0002_ops_meta",
        "0003_audit",
        "0004_analytics_schema",
        "0004_case_write_constraints",
        "0005_audit_log_replay_action",
        "0006_dialogue_and_handoff",
        "0006_signin_audit",
        "0007_signin_audit_agent_id",
        "0008_audit_log_console_read_actions",
        "0008_console_read_support",
        "0009_handoff_outbox_content_fingerprint",
        "0010_customer_repeat_complainer",
        "0012_handoff_outbox_risk_threshold",
    )

    again = apply_migrations(dsn)
    assert again == ()

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
        tables = {row[0] for row in cur.fetchall()}
    assert {
        "customers",
        "products",
        "transactions",
        "cases",
        "ops_meta",
        "audit_log",
        "dialogue_state",
        "handoff_outbox",
        "handoff_actions",
        "handoff_open_questions",
        "handoff_reason_codes",
        "handoff_sources",
        "schema_migrations",
    } <= tables


@pytest.mark.integration
def test_a_value_outside_the_closed_set_is_rejected_by_the_database() -> None:
    """The migration's CHECK constraints hold even if the tool layer's own validation does not.

    Scoped to its own row, cleaned up first, so it does not depend on or disturb what other
    tests leave in the shared ``customers`` table.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)

    customer_id = "CHECK_TEST_1"
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO customers "
                "(customer_id, first_name, last_name, country, customer_status) "
                "VALUES (%s, 'A', 'B', 'CO', 'NotARealStatus')",
                (customer_id,),
            )


@pytest.mark.integration
def test_a_changed_migration_file_is_refused(tmp_path: Path) -> None:
    """A migration already applied is never silently re-applied under a changed file.

    Scoped to its own version and table: the shared ``schema_migrations`` table (and the real
    ``0001_serving_store`` row and tables it tracks) is left untouched, so this test can run
    against the same database another test or ``make migrate`` has already applied to.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")

    version = "9999_isolation_test"
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS isolation_test_table")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version TEXT PRIMARY KEY, checksum TEXT NOT NULL, "
            "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        cur.execute("DELETE FROM schema_migrations WHERE version = %s", (version,))

    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / f"{version}.sql").write_text(
        "CREATE TABLE isolation_test_table (id INT)", encoding="utf-8"
    )
    apply_migrations(dsn, directory=directory)

    (directory / f"{version}.sql").write_text(
        "CREATE TABLE isolation_test_table (id BIGINT)", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="has changed since it was applied"):
        apply_migrations(dsn, directory=directory)
