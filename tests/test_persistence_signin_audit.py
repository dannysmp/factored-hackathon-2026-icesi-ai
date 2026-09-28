"""
Sign-In Audit Store Tests
==========================

Component: ``app.persistence.signin_audit``. Needs a real, migrated Postgres: append-only is a
store guarantee (migration 0006's trigger), not something a fake could prove. Marked
``integration``, skipped when ``DATABASE_URL`` is not set.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import psycopg
import pytest

from app.persistence.migrate import apply_migrations
from app.persistence.signin_audit import PostgresSignInAuditSink
from app.security.signin_audit import (
    SignInAudience,
    SignInAuditRecord,
    SignInOutcome,
    SignInReasonCode,
)

OCCURRED_AT = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


def _record(
    *,
    trace_id: str = "TRACE-1",
    occurred_at: datetime = OCCURRED_AT,
    audience: SignInAudience = SignInAudience.CUSTOMER,
    outcome: SignInOutcome = SignInOutcome.REFUSED,
    reason_code: SignInReasonCode = SignInReasonCode.INVALID_ACCESS_CODE,
    client_address_hash: str = "0" * 64,
    persona_slug: str | None = None,
    resolved_customer_id: str | None = None,
    session_id: str | None = None,
) -> SignInAuditRecord:
    return SignInAuditRecord(
        trace_id=trace_id,
        occurred_at=occurred_at,
        audience=audience,
        outcome=outcome,
        reason_code=reason_code,
        client_address_hash=client_address_hash,
        persona_slug=persona_slug,
        resolved_customer_id=resolved_customer_id,
        session_id=session_id,
    )


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(value)
    with psycopg.connect(value) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute("TRUNCATE TABLE signin_audit")
        conn.commit()
    return value


@pytest.mark.integration
def test_a_refused_attempt_is_written_and_readable_back(dsn: str) -> None:
    sink = PostgresSignInAuditSink(dsn)

    sink.record(_record())

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT trace_id, audience, outcome, reason_code, session_id FROM signin_audit")
        rows = cur.fetchall()
    assert rows == [("TRACE-1", "customer", "refused", "invalid_access_code", None)]


@pytest.mark.integration
def test_an_issued_attempt_carries_the_persona_customer_and_session(dsn: str) -> None:
    sink = PostgresSignInAuditSink(dsn)

    sink.record(
        _record(
            outcome=SignInOutcome.ISSUED,
            reason_code=SignInReasonCode.ISSUED,
            persona_slug="ana",
            resolved_customer_id="CLI-1",
            session_id="SESSION-1",
        )
    )

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT persona_slug, resolved_customer_id, session_id FROM signin_audit")
        rows = cur.fetchall()
    assert rows == [("ana", "CLI-1", "SESSION-1")]


@pytest.mark.integration
def test_the_store_rejects_an_issued_outcome_with_no_session_id(dsn: str) -> None:
    """The check constraint enforces session_id iff issued, independent of the caller."""
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        psycopg.connect(dsn, autocommit=True) as conn,
        conn.cursor() as cur,
    ):
        cur.execute(
            "INSERT INTO signin_audit (trace_id, occurred_at_utc, audience, "
            "client_address_hash, outcome, reason_code) VALUES "
            "('TRACE-1', now(), 'customer', %s, 'issued', 'issued')",
            ("0" * 64,),
        )


@pytest.mark.integration
def test_the_signin_audit_refuses_an_update_at_the_store(dsn: str) -> None:
    PostgresSignInAuditSink(dsn).record(_record())

    with (
        psycopg.connect(dsn, autocommit=True) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
    ):
        cur.execute("UPDATE signin_audit SET reason_code = 'rate_limited'")


@pytest.mark.integration
def test_the_signin_audit_refuses_a_delete_at_the_store(dsn: str) -> None:
    PostgresSignInAuditSink(dsn).record(_record())

    with (
        psycopg.connect(dsn, autocommit=True) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
    ):
        cur.execute("DELETE FROM signin_audit")


@pytest.mark.integration
def test_the_signin_audit_refuses_a_truncate_at_the_store_even_for_the_table_owner(
    dsn: str,
) -> None:
    PostgresSignInAuditSink(dsn).record(_record())

    with (
        psycopg.connect(dsn, autocommit=True) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
    ):
        cur.execute("TRUNCATE TABLE signin_audit")


def test_a_write_that_cannot_complete_raises_rather_than_dropping_the_entry() -> None:
    """Fail closed: an unreachable store raises, matching ``SignInAuditSink.record``'s contract."""
    sink = PostgresSignInAuditSink("postgresql://unreachable.invalid/nowhere")

    with pytest.raises(psycopg.Error):
        sink.record(_record())
