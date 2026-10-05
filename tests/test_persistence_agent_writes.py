"""
Postgres Agent Writes Tests
===========================

Component: ``app.persistence.agent_writes``. Needs a real, migrated Postgres: each write reads a
real ``handoff_outbox`` row for its identity, writes through the real tables, and audits through
the real, append-only ``audit_log`` table.
"""

from __future__ import annotations

# Standard libraries
import os
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

# Third-party libraries
import psycopg
import pytest

# Local modules
from app.conversation.handoff import HandoffContent
from app.domain.calendar import DateOrigin, DomainCalendar
from app.domain.policy.models import ReasonCode
from app.persistence.agent_writes import CaseStatusTerminal, PostgresAgentWrites
from app.persistence.audit import PostgresAuditSink
from app.persistence.handoff_outbox import PostgresHandoffOutbox
from app.persistence.handoff_queue import PostgresHandoffQueue
from app.persistence.migrate import apply_migrations
from contracts.service_v1.audit import AuditAction
from contracts.service_v1.cases import CaseStatus
from contracts.service_v1.handoff import HandoffTrigger

_CREATED_AT = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)
_NOW = datetime(2026, 6, 20, 9, 0, tzinfo=UTC)
_TODAY = DomainCalendar(reference_date=date(2026, 6, 20), origin=DateOrigin.SETTING)
_AGENT_SESSION_ID = "sess-agent-1"
_POLICY_VERSION = "2"


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
        "policy_version": _POLICY_VERSION,
    }
    return HandoffContent(**{**values, **changes})  # type: ignore[arg-type]


def _insert_case_row(dsn: str, **overrides: str) -> None:
    """A case row inserted directly, matching a filed case's own shape
    (``tests/test_persistence_cases.py``'s identical helper) — this module never creates one."""
    values = {
        "case_number": "CASE-DIRECT",
        "customer_id": "CLI-1234",
        "transaction_id": "TRX-A1",
        "session_id": "SESSION-OTHER",
        "idempotency_key": "IDEMP-DIRECT",
        "status": "Open",
        "category": "unrecognized_charge",
        "reason_code": "eligible",
        **overrides,
    }
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cases (case_number, customer_id, transaction_id, session_id, "
            "idempotency_key, status, category, amount, currency, amount_provenance, "
            "domain_date, expected_first_response_date, created_at_utc, policy_version, "
            "reason_code, language) VALUES "
            "(%(case_number)s, %(customer_id)s, %(transaction_id)s, %(session_id)s, "
            "%(idempotency_key)s, %(status)s, %(category)s, 100.00, 'USD', 'reported', "
            "'2026-06-01', '2026-10-01', '2026-06-01 09:00:00+00', %(policy_version)s, "
            "%(reason_code)s, 'en')",
            {**values, "policy_version": _POLICY_VERSION},
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
            "TRUNCATE TABLE handoff_notes, handoff_actions, handoff_open_questions, "
            "handoff_reason_codes, handoff_sources, handoff_outbox, cases, transactions, "
            "products, customers, audit_log CASCADE"
        )
        conn.commit()
    with psycopg.connect(value, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customers (customer_id, first_name, last_name, country, "
            "customer_status) VALUES ('CLI-1234', 'Ana', 'Last', 'México', 'Active')"
        )
        cur.execute(
            "INSERT INTO products (product_id, customer_id, product_type, last4, "
            "product_status) VALUES ('PRD-A', 'CLI-1234', 'Cuenta Corriente', '1234', 'Active')"
        )
        cur.execute(
            "INSERT INTO transactions (transaction_id, customer_id, product_id, "
            "transaction_date, transaction_type, merchant_name, amount, currency, amount_usd, "
            "amount_usd_provenance, transaction_status) VALUES "
            "('TRX-A1', 'CLI-1234', 'PRD-A', '2026-06-08 09:00:00', 'Purchase', 'A Merchant', "
            "100.00, 'USD', 100.00, 'reported', 'Approved')"
        )
    return value


@pytest.fixture
def outbox(dsn: str) -> PostgresHandoffOutbox:
    return PostgresHandoffOutbox(dsn)


@pytest.fixture
def queue(dsn: str) -> PostgresHandoffQueue:
    return PostgresHandoffQueue(dsn, contact_days_priority=1, contact_days_default=2)


@pytest.fixture
def writes(dsn: str, queue: PostgresHandoffQueue) -> PostgresAgentWrites:
    return PostgresAgentWrites(
        dsn,
        sink=PostgresAuditSink(dsn),
        queue=queue,
        calendar=_TODAY,
        clock=lambda: _NOW,
    )


def _ticket(
    outbox: PostgresHandoffOutbox, *, existing_case_number: str | None = None
) -> tuple[str, str]:
    """Writes a real outbox row and returns ``(ticket_ref, trace_id)``."""
    packet = outbox.record(
        _content(existing_case_number=existing_case_number),
        session_id="sess-customer-original",
        turn_id="turn-1",
        trace_id="trace-1",
    )
    return packet.ticket_ref, "trace-1"


def _fetch_audit(dsn: str, action: AuditAction) -> tuple[str, str, str, str | None]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT trace_id, customer_id, session_id, agent_id FROM audit_log WHERE action = %s",
            (action.value,),
        )
        row = cur.fetchone()
    assert row is not None
    return row


# -----------------------------------------------------------------------------
# Claim and release
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_claim_ticket_sets_claimed_by_and_returns_the_updated_item(
    outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    ticket_ref, _ = _ticket(outbox)

    item = writes.claim_ticket(
        agent_id="AGT-1", session_id=_AGENT_SESSION_ID, ticket_ref=ticket_ref
    )

    assert item is not None
    assert item.claimed_by == "AGT-1"


@pytest.mark.integration
def test_claim_ticket_overwrites_a_prior_claim(
    outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    ticket_ref, _ = _ticket(outbox)
    writes.claim_ticket(agent_id="AGT-1", session_id=_AGENT_SESSION_ID, ticket_ref=ticket_ref)

    item = writes.claim_ticket(
        agent_id="AGT-2", session_id=_AGENT_SESSION_ID, ticket_ref=ticket_ref
    )

    assert item is not None
    assert item.claimed_by == "AGT-2"


@pytest.mark.integration
def test_claim_ticket_audits_with_the_agents_identity(
    dsn: str, outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    """The audit row must actually carry ``AGT-1`` and the
    ticket's real trace/customer identity, not a placeholder — reverting the write's ``agent_id``
    argument or the audit call itself would fail this."""
    ticket_ref, trace_id = _ticket(outbox)

    writes.claim_ticket(agent_id="AGT-1", session_id=_AGENT_SESSION_ID, ticket_ref=ticket_ref)

    written_trace, written_customer, written_session, written_agent = _fetch_audit(
        dsn, AuditAction.TICKET_CLAIMED
    )
    assert written_trace == trace_id
    assert written_customer == "CLI-1234"
    assert written_session == _AGENT_SESSION_ID
    assert written_agent == "AGT-1"


@pytest.mark.integration
def test_claim_ticket_returns_none_for_an_unknown_ticket(writes: PostgresAgentWrites) -> None:
    item = writes.claim_ticket(
        agent_id="AGT-1", session_id=_AGENT_SESSION_ID, ticket_ref="T-NOT-A-REAL-TICKET"
    )

    assert item is None


@pytest.mark.integration
def test_release_ticket_clears_the_claim_and_returns_the_updated_item(
    outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    ticket_ref, _ = _ticket(outbox)
    writes.claim_ticket(agent_id="AGT-1", session_id=_AGENT_SESSION_ID, ticket_ref=ticket_ref)

    item = writes.release_ticket(
        agent_id="AGT-1", session_id=_AGENT_SESSION_ID, ticket_ref=ticket_ref
    )

    assert item is not None
    assert item.claimed_by is None


@pytest.mark.integration
def test_release_ticket_audits_with_the_agents_identity(
    dsn: str, outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    ticket_ref, trace_id = _ticket(outbox)

    writes.release_ticket(agent_id="AGT-1", session_id=_AGENT_SESSION_ID, ticket_ref=ticket_ref)

    written_trace, written_customer, _, written_agent = _fetch_audit(
        dsn, AuditAction.TICKET_RELEASED
    )
    assert written_trace == trace_id
    assert written_customer == "CLI-1234"
    assert written_agent == "AGT-1"


@pytest.mark.integration
def test_release_ticket_returns_none_for_an_unknown_ticket(writes: PostgresAgentWrites) -> None:
    item = writes.release_ticket(
        agent_id="AGT-1", session_id=_AGENT_SESSION_ID, ticket_ref="T-NOT-A-REAL-TICKET"
    )

    assert item is None


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_add_note_returns_the_written_note(
    outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    ticket_ref, _ = _ticket(outbox)

    note = writes.add_note(
        agent_id="AGT-1",
        session_id=_AGENT_SESSION_ID,
        ticket_ref=ticket_ref,
        note_text="Called the customer back.",
    )

    assert note is not None
    assert note.agent_id == "AGT-1"
    assert note.note_text == "Called the customer back."
    assert note.created_at == _NOW


@pytest.mark.integration
def test_add_note_reads_the_written_row_back_rather_than_echoing_the_input(
    outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    """Verify-before-report: the returned ``Note`` comes from a fresh, independent ``SELECT``
    issued after the insert's own connection closes, not from the caller's own arguments — proven
    by counting connections opened: one for the insert, a second the audit sink opens for its own
    write, a third, independent one for the read-back."""
    ticket_ref, _ = _ticket(outbox)
    real_connect = psycopg.connect
    calls = 0

    def counting_connect(*args: object, **kwargs: object) -> psycopg.Connection:
        nonlocal calls
        calls += 1
        return real_connect(*args, **kwargs)  # type: ignore[arg-type]

    with patch("app.persistence.agent_writes.psycopg.connect", side_effect=counting_connect):
        writes.add_note(
            agent_id="AGT-1",
            session_id=_AGENT_SESSION_ID,
            ticket_ref=ticket_ref,
            note_text="Called back.",
        )

    assert calls == 3


@pytest.mark.integration
def test_add_note_orders_successive_notes(
    dsn: str, outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    ticket_ref, _ = _ticket(outbox)

    writes.add_note(
        agent_id="AGT-1", session_id=_AGENT_SESSION_ID, ticket_ref=ticket_ref, note_text="First."
    )
    writes.add_note(
        agent_id="AGT-2", session_id=_AGENT_SESSION_ID, ticket_ref=ticket_ref, note_text="Second."
    )

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ord, note_text FROM handoff_notes WHERE ticket_ref = %s ORDER BY ord",
            (ticket_ref,),
        )
        rows = cur.fetchall()
    assert rows == [(0, "First."), (1, "Second.")]


@pytest.mark.integration
def test_add_note_audits_with_the_agents_identity(
    dsn: str, outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    ticket_ref, trace_id = _ticket(outbox)

    writes.add_note(
        agent_id="AGT-1",
        session_id=_AGENT_SESSION_ID,
        ticket_ref=ticket_ref,
        note_text="Called back.",
    )

    written_trace, written_customer, _, written_agent = _fetch_audit(
        dsn, AuditAction.TICKET_NOTE_ADDED
    )
    assert written_trace == trace_id
    assert written_customer == "CLI-1234"
    assert written_agent == "AGT-1"


@pytest.mark.integration
def test_add_note_returns_none_for_an_unknown_ticket(writes: PostgresAgentWrites) -> None:
    note = writes.add_note(
        agent_id="AGT-1",
        session_id=_AGENT_SESSION_ID,
        ticket_ref="T-NOT-A-REAL-TICKET",
        note_text="Should not be written.",
    )

    assert note is None


# -----------------------------------------------------------------------------
# Case status
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_set_case_status_updates_the_case_and_returns_the_result(
    dsn: str, outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    _insert_case_row(dsn, case_number="CASE-1", status="Open")
    ticket_ref, _ = _ticket(outbox, existing_case_number="CASE-1")

    result = writes.set_case_status(
        agent_id="AGT-1",
        session_id=_AGENT_SESSION_ID,
        ticket_ref=ticket_ref,
        status=CaseStatus.IN_REVIEW,
    )

    assert result is not None
    assert result.case_number == "CASE-1"
    assert result.status is CaseStatus.IN_REVIEW
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM cases WHERE case_number = 'CASE-1'")
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "In Review"


@pytest.mark.integration
def test_set_case_status_reads_the_written_row_back_rather_than_echoing_the_input(
    dsn: str, outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    """Verify-before-report: the returned ``CaseStatusResult`` comes from a fresh, independent
    ``SELECT`` issued after the update's own connection closes, not from the caller's own
    arguments — proven by counting connections opened: one for the update, a second the audit sink
    opens for its own write, a third, independent one for the read-back."""
    _insert_case_row(dsn, case_number="CASE-1", status="Open")
    ticket_ref, _ = _ticket(outbox, existing_case_number="CASE-1")
    real_connect = psycopg.connect
    calls = 0

    def counting_connect(*args: object, **kwargs: object) -> psycopg.Connection:
        nonlocal calls
        calls += 1
        return real_connect(*args, **kwargs)  # type: ignore[arg-type]

    with patch("app.persistence.agent_writes.psycopg.connect", side_effect=counting_connect):
        writes.set_case_status(
            agent_id="AGT-1",
            session_id=_AGENT_SESSION_ID,
            ticket_ref=ticket_ref,
            status=CaseStatus.IN_REVIEW,
        )

    assert calls == 3


@pytest.mark.integration
def test_set_case_status_setting_the_current_value_is_a_harmless_audited_noop(
    dsn: str, outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    _insert_case_row(dsn, case_number="CASE-1", status="Open")
    ticket_ref, _ = _ticket(outbox, existing_case_number="CASE-1")

    result = writes.set_case_status(
        agent_id="AGT-1",
        session_id=_AGENT_SESSION_ID,
        ticket_ref=ticket_ref,
        status=CaseStatus.OPEN,
    )

    assert result is not None
    assert result.status is CaseStatus.OPEN
    _, _, _, written_agent = _fetch_audit(dsn, AuditAction.CASE_STATUS_SET)
    assert written_agent == "AGT-1"


@pytest.mark.integration
def test_set_case_status_audits_with_the_agents_identity(
    dsn: str, outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    _insert_case_row(dsn, case_number="CASE-1", status="Open")
    ticket_ref, trace_id = _ticket(outbox, existing_case_number="CASE-1")

    writes.set_case_status(
        agent_id="AGT-1",
        session_id=_AGENT_SESSION_ID,
        ticket_ref=ticket_ref,
        status=CaseStatus.RESOLVED,
    )

    written_trace, written_customer, _, written_agent = _fetch_audit(
        dsn, AuditAction.CASE_STATUS_SET
    )
    assert written_trace == trace_id
    assert written_customer == "CLI-1234"
    assert written_agent == "AGT-1"


@pytest.mark.integration
def test_set_case_status_returns_none_for_an_unknown_ticket(writes: PostgresAgentWrites) -> None:
    result = writes.set_case_status(
        agent_id="AGT-1",
        session_id=_AGENT_SESSION_ID,
        ticket_ref="T-NOT-A-REAL-TICKET",
        status=CaseStatus.RESOLVED,
    )

    assert result is None


@pytest.mark.integration
def test_set_case_status_returns_none_when_the_ticket_names_no_case(
    outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    ticket_ref, _ = _ticket(outbox, existing_case_number=None)

    result = writes.set_case_status(
        agent_id="AGT-1",
        session_id=_AGENT_SESSION_ID,
        ticket_ref=ticket_ref,
        status=CaseStatus.RESOLVED,
    )

    assert result is None


@pytest.mark.integration
def test_set_case_status_returns_none_when_the_named_case_does_not_resolve(
    outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    """A ticket can name a case number no ``cases`` row actually has (a data inconsistency, not a
    normal path) — this is still a plain not-found, the same as a ticket naming no case at all."""
    ticket_ref, _ = _ticket(outbox, existing_case_number="CASE-DOES-NOT-EXIST")

    result = writes.set_case_status(
        agent_id="AGT-1",
        session_id=_AGENT_SESSION_ID,
        ticket_ref=ticket_ref,
        status=CaseStatus.RESOLVED,
    )

    assert result is None


@pytest.mark.integration
@pytest.mark.parametrize("terminal_status", [CaseStatus.RESOLVED, CaseStatus.REJECTED])
def test_set_case_status_refuses_a_case_already_in_a_terminal_status(
    dsn: str,
    outbox: PostgresHandoffOutbox,
    writes: PostgresAgentWrites,
    terminal_status: CaseStatus,
) -> None:
    _insert_case_row(dsn, case_number="CASE-1", status=terminal_status.value)
    ticket_ref, _ = _ticket(outbox, existing_case_number="CASE-1")

    with pytest.raises(CaseStatusTerminal):
        writes.set_case_status(
            agent_id="AGT-1",
            session_id=_AGENT_SESSION_ID,
            ticket_ref=ticket_ref,
            status=CaseStatus.IN_REVIEW,
        )

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM cases WHERE case_number = 'CASE-1'")
        row = cur.fetchone()
    assert row is not None
    assert row[0] == terminal_status.value


@pytest.mark.integration
def test_set_case_status_never_creates_a_case_row(
    dsn: str, outbox: PostgresHandoffOutbox, writes: PostgresAgentWrites
) -> None:
    """A case-status write only ever ``UPDATE``s (it never creates one): proven here by
    calling it against a real, pre-existing case and confirming the row count never grows, and
    structurally below by confirming the module's own source has no ``INSERT INTO cases``."""
    _insert_case_row(dsn, case_number="CASE-1", status="Open")
    ticket_ref, _ = _ticket(outbox, existing_case_number="CASE-1")

    writes.set_case_status(
        agent_id="AGT-1",
        session_id=_AGENT_SESSION_ID,
        ticket_ref=ticket_ref,
        status=CaseStatus.RESOLVED,
    )

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM cases")
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 1


def test_the_module_never_inserts_into_cases() -> None:
    """Structural companion to the integration test above: this module has no statement that
    could create a case row, whatever path a future change takes through it."""
    source = Path("app/persistence/agent_writes.py").read_text(encoding="utf-8")
    assert "INSERT INTO cases" not in source
