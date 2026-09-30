"""
Audit Sink Tests
================

Component: ``app.persistence.audit``. Needs a real, migrated Postgres: append-only is a store
guarantee (migration 0003's trigger), not something a fake could prove. Marked ``integration``,
skipped when ``DATABASE_URL`` is not set.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

import psycopg
import pytest

from app.domain.policy.models import ReasonCode
from app.persistence.audit import PostgresAuditSink
from app.persistence.migrate import apply_migrations
from contracts.service_v1.audit import AuditAction, AuditRecord

OCCURRED_AT = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
DOMAIN_DATE = date(2026, 6, 18)


def _record(**overrides: object) -> AuditRecord:
    values: dict[str, object] = {
        "trace_id": "TRACE-1",
        "customer_id": "CLI-1",
        "session_id": "SESSION-1",
        "action": AuditAction.TRANSACTION_VIEWED,
        "reason_code": None,
        "policy_version": None,
        "tool_result_hash": "0" * 64,
        "occurred_at": OCCURRED_AT,
        "domain_date": DOMAIN_DATE,
    }
    values.update(overrides)
    return AuditRecord(**values)


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(value)
    # TRUNCATE is refused at the store (migration 0003), including for this reset: the session's
    # own replication role is switched off for it, since a trigger created without ENABLE REPLICA
    # or ENABLE ALWAYS does not fire under 'replica'.
    with psycopg.connect(value) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute("TRUNCATE TABLE audit_log")
        conn.commit()
    return value


@pytest.mark.integration
def test_a_record_is_written_and_readable_back(dsn: str) -> None:
    sink = PostgresAuditSink(dsn)

    sink.record(_record())

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT trace_id, customer_id, session_id, action FROM audit_log")
        rows = cur.fetchall()
    assert rows == [("TRACE-1", "CLI-1", "SESSION-1", "transaction_viewed")]


@pytest.mark.integration
def test_a_policy_decision_carries_its_reason_code_and_policy_version(dsn: str) -> None:
    sink = PostgresAuditSink(dsn)

    sink.record(
        _record(
            action=AuditAction.DISPUTE_EVALUATED,
            reason_code=ReasonCode.ELIGIBLE,
            policy_version="1",
        )
    )

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT reason_code, policy_version FROM audit_log")
        rows = cur.fetchall()
    assert rows == [("eligible", "1")]


@pytest.mark.integration
def test_an_agent_action_writes_the_agents_own_identity(dsn: str) -> None:
    """ADR-17: an agent action's own identity survives to the store, alongside its session."""
    sink = PostgresAuditSink(dsn)

    sink.record(_record(action=AuditAction.PACKET_VIEWED, agent_id="AGT-1"))

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT agent_id FROM audit_log")
        rows = cur.fetchall()
    assert rows == [("AGT-1",)]


@pytest.mark.integration
def test_a_customer_action_writes_no_agent_id(dsn: str) -> None:
    """A customer-originated action names no agent: the column stays NULL, never a placeholder."""
    sink = PostgresAuditSink(dsn)

    sink.record(_record())

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT agent_id FROM audit_log")
        rows = cur.fetchall()
    assert rows == [(None,)]


@pytest.mark.integration
@pytest.mark.parametrize(
    "action", [AuditAction.PACKET_VIEWED, AuditAction.TIMELINE_VIEWED], ids=lambda a: a.value
)
def test_an_agent_console_read_is_accepted_by_the_stores_own_check_constraint(
    dsn: str, action: AuditAction
) -> None:
    """Migration 0008 widened the CHECK constraint, not just the Python enum: a value the store
    itself refused before now writes cleanly."""
    sink = PostgresAuditSink(dsn)

    sink.record(_record(action=action))

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT action FROM audit_log")
        rows = cur.fetchall()
    assert rows == [(action.value,)]


@pytest.mark.integration
def test_the_audit_log_refuses_an_update_at_the_store(dsn: str) -> None:
    """AC-E4-21: fails at the store, not only in code."""
    PostgresAuditSink(dsn).record(_record())

    with (
        psycopg.connect(dsn, autocommit=True) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
    ):
        cur.execute("UPDATE audit_log SET customer_id = 'CLI-2'")


@pytest.mark.integration
def test_the_audit_log_refuses_a_delete_at_the_store(dsn: str) -> None:
    """AC-E4-21: fails at the store, not only in code."""
    PostgresAuditSink(dsn).record(_record())

    with (
        psycopg.connect(dsn, autocommit=True) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
    ):
        cur.execute("DELETE FROM audit_log")


@pytest.mark.integration
def test_the_audit_log_refuses_a_truncate_at_the_store_even_for_the_table_owner(dsn: str) -> None:
    """AC-E4-21: TRUNCATE fires no row-level trigger, so it needs (and has) its own; this
    project has no role separation, so the connecting role is also the table's owner, and
    Postgres normally lets an owner TRUNCATE regardless of any row-level rule."""
    PostgresAuditSink(dsn).record(_record())

    with (
        psycopg.connect(dsn, autocommit=True) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
    ):
        cur.execute("TRUNCATE TABLE audit_log")


def test_a_write_that_cannot_complete_raises_rather_than_dropping_the_entry() -> None:
    """Fail closed: an unreachable store raises, matching ``AuditSink.record``'s own contract."""
    sink = PostgresAuditSink("postgresql://unreachable.invalid/nowhere")

    with pytest.raises(psycopg.Error):
        sink.record(_record())
