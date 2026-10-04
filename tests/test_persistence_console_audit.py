"""
Console Audit Sink Tests
========================

Component: ``app.persistence.console_audit``. Needs a real, migrated Postgres: the sink's own
``customer_id``/``trace_id`` resolution reads a real ``handoff_outbox`` row, and the write itself
goes through the real, append-only ``audit_log`` table.
"""

from __future__ import annotations

# Standard libraries
import os
from datetime import UTC, date, datetime

# Third-party libraries
import psycopg
import pytest

# Local modules
from app.conversation.handoff import HandoffContent
from app.domain.calendar import DateOrigin, DomainCalendar
from app.domain.policy.models import ReasonCode
from app.persistence.audit import PostgresAuditSink
from app.persistence.console_audit import PostgresConsoleAuditSink, TicketNotFoundError
from app.persistence.handoff_outbox import PostgresHandoffOutbox
from app.persistence.migrate import apply_migrations
from contracts.service_v1.audit import AuditAction
from contracts.service_v1.console import TimelineEntry
from contracts.service_v1.handoff import CustomerLabel, Evidence, HandoffPacket, HandoffTrigger

_CREATED_AT = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)
_TODAY = DomainCalendar(reference_date=date(2026, 6, 20), origin=DateOrigin.SETTING)
_AGENT_SESSION_ID = "sess-agent-1"


def _content(**changes: object) -> HandoffContent:
    values: dict[str, object] = {
        "reference_date": date(2026, 6, 18),
        "created_at": _CREATED_AT,
        "language": "es",
        "trigger": HandoffTrigger.CUSTOMER_REQUEST,
        "first_name": "Ana",
        "customer_id": "CLI-1234",
        "request_summary": "Wants to speak with a person.",
        "reason_codes": (ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,),
        "policy_version": "2",
    }
    return HandoffContent(**{**values, **changes})  # type: ignore[arg-type]


def _packet_for(ticket_ref: str, *, language: str = "es") -> HandoffPacket:
    """A minimal, self-consistent packet for `packet_viewed`'s own `result` payload — the sink
    only hashes it, it never validates it against the outbox row."""
    return HandoffPacket(
        ticket_ref=ticket_ref,
        reference_date=date(2026, 6, 18),
        created_at=_CREATED_AT,
        language=language,
        needs_language_routing=language != "es",
        trigger=HandoffTrigger.CUSTOMER_REQUEST,
        customer=CustomerLabel(first_name="Ana", masked_id="****34"),
        request_summary="Wants to speak with a person.",
        evidence=Evidence(reason_codes=(), policy_version="2"),
    )


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(value)
    with psycopg.connect(value) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute(
            "TRUNCATE TABLE handoff_actions, handoff_open_questions, handoff_reason_codes, "
            "handoff_sources, handoff_outbox, audit_log CASCADE"
        )
        conn.commit()
    return value


@pytest.fixture
def outbox(dsn: str) -> PostgresHandoffOutbox:
    return PostgresHandoffOutbox(dsn)


@pytest.fixture
def sink(dsn: str) -> PostgresConsoleAuditSink:
    return PostgresConsoleAuditSink(
        dsn,
        sink=PostgresAuditSink(dsn),
        calendar=_TODAY,
        clock=lambda: _CREATED_AT,
    )


def _ticket(outbox: PostgresHandoffOutbox, *, customer_id: str = "CLI-1234") -> tuple[str, str]:
    """Writes a real outbox row and returns ``(ticket_ref, trace_id)``."""
    packet = outbox.record(
        _content(customer_id=customer_id),
        session_id="sess-customer-original",
        turn_id="turn-1",
        trace_id="trace-1",
    )
    return packet.ticket_ref, "trace-1"


def _fetch_record(dsn: str, action: AuditAction) -> tuple[str, str, str, str]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT trace_id, customer_id, session_id, action FROM audit_log WHERE action = %s",
            (action.value,),
        )
        row = cur.fetchone()
    assert row is not None
    return row


@pytest.mark.integration
def test_packet_viewed_writes_the_tickets_real_customer_id_and_the_agents_own_session(
    dsn: str, outbox: PostgresHandoffOutbox, sink: PostgresConsoleAuditSink
) -> None:
    ticket_ref, trace_id = _ticket(outbox, customer_id="CLI-1234")

    sink.packet_viewed(
        agent_id="AGT-1",
        session_id=_AGENT_SESSION_ID,
        ticket_ref=ticket_ref,
        packet=_packet_for(ticket_ref),
    )

    written_trace, written_customer, written_session, written_action = _fetch_record(
        dsn, AuditAction.PACKET_VIEWED
    )
    assert written_trace == trace_id
    # The ticket's real customer_id, not the masked label the packet itself carries.
    assert written_customer == "CLI-1234"
    # The agent's own session, never "sess-customer-original" (the outbox's stored, customer
    # session from the original conversation).
    assert written_session == _AGENT_SESSION_ID
    assert written_action == AuditAction.PACKET_VIEWED.value


@pytest.mark.integration
def test_packet_viewed_writes_the_agents_own_identity_too(
    dsn: str, outbox: PostgresHandoffOutbox, sink: PostgresConsoleAuditSink
) -> None:
    """An agent read is audited with the agent's own identity, not only the session that
    carried it — a session is ephemeral, but the identity must survive it."""
    ticket_ref, _ = _ticket(outbox, customer_id="CLI-9999")

    sink.packet_viewed(
        agent_id="AGT-42",
        session_id=_AGENT_SESSION_ID,
        ticket_ref=ticket_ref,
        packet=_packet_for(ticket_ref),
    )

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT agent_id FROM audit_log WHERE action = %s", (AuditAction.PACKET_VIEWED.value,)
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "AGT-42"


@pytest.mark.integration
def test_timeline_viewed_writes_a_record_too(
    dsn: str, outbox: PostgresHandoffOutbox, sink: PostgresConsoleAuditSink
) -> None:
    ticket_ref, trace_id = _ticket(outbox)
    entries: tuple[TimelineEntry, ...] = ()

    sink.timeline_viewed(
        agent_id="AGT-1", session_id=_AGENT_SESSION_ID, ticket_ref=ticket_ref, timeline=entries
    )

    written_trace, written_customer, written_session, written_action = _fetch_record(
        dsn, AuditAction.TIMELINE_VIEWED
    )
    assert written_trace == trace_id
    assert written_customer == "CLI-1234"
    assert written_session == _AGENT_SESSION_ID
    assert written_action == AuditAction.TIMELINE_VIEWED.value


@pytest.mark.integration
def test_tool_result_hash_reflects_the_actual_packet_shown(
    dsn: str, outbox: PostgresHandoffOutbox, sink: PostgresConsoleAuditSink
) -> None:
    """Revert-check: two different packets must hash differently — a constant placeholder would
    make this test fail."""
    ticket_ref, _ = _ticket(outbox)

    sink.packet_viewed(
        agent_id="AGT-1",
        session_id=_AGENT_SESSION_ID,
        ticket_ref=ticket_ref,
        packet=_packet_for(ticket_ref, language="es"),
    )
    sink.packet_viewed(
        agent_id="AGT-1",
        session_id=_AGENT_SESSION_ID,
        ticket_ref=ticket_ref,
        packet=_packet_for(ticket_ref, language="en"),
    )

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT tool_result_hash FROM audit_log WHERE action = %s "
            "ORDER BY occurred_at_utc, tool_result_hash",
            (AuditAction.PACKET_VIEWED.value,),
        )
        rows = cur.fetchall()
    assert len(rows) == 2
    first_hash, second_hash = rows[0][0], rows[1][0]
    assert first_hash != second_hash
    assert len(first_hash) == len(second_hash) == 64


@pytest.mark.integration
def test_raises_for_a_ticket_reference_not_on_file(sink: PostgresConsoleAuditSink) -> None:
    with pytest.raises(TicketNotFoundError):
        sink.packet_viewed(
            agent_id="AGT-1",
            session_id=_AGENT_SESSION_ID,
            ticket_ref="T-NOT-A-REAL-TICKET",
            packet=_packet_for("T-NOT-A-REAL-TICKET"),
        )
