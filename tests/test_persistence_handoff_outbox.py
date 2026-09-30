"""
Postgres Handoff Outbox Tests
==============================

Component: ``app.persistence.handoff_outbox``. Needs a real, migrated Postgres; marked
``integration``, skipped when ``DATABASE_URL`` is not set.
"""

from __future__ import annotations

# Standard libraries
import os
import threading
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch

# Third-party libraries
import psycopg
import psycopg.errors
import pytest

# Local modules
from app.conversation.handoff import HandoffContent
from app.domain.policy.models import DisputeCategory, ReasonCode, TransactionStatus
from app.persistence.handoff_outbox import HandoffReplayMismatch, PostgresHandoffOutbox
from app.persistence.migrate import apply_migrations
from contracts.service_v1.envelope import (
    LocalizedTitle,
    Money,
    ProductLabel,
    RiskEvidence,
    Slot,
    SourceRef,
    TransactionFact,
)
from contracts.service_v1.handoff import (
    ActionRecord,
    HandoffPacket,
    HandoffTrigger,
    OpenQuestion,
)

_REFERENCE_DATE = date(2026, 6, 18)
_CREATED_AT = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)


def _content(**changes: object) -> HandoffContent:
    values: dict[str, object] = {
        "reference_date": _REFERENCE_DATE,
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


def _record(
    outbox: PostgresHandoffOutbox, content: HandoffContent, *, turn_id: str = "t-1"
) -> HandoffPacket:
    return outbox.record(content, session_id="s-1", turn_id=turn_id, trace_id="trace-1")


@pytest.fixture
def outbox() -> PostgresHandoffOutbox:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE handoff_actions, handoff_open_questions, handoff_reason_codes, "
            "handoff_sources, handoff_outbox CASCADE"
        )
    return PostgresHandoffOutbox(dsn)


@pytest.mark.integration
def test_record_writes_a_retrievable_row(outbox: PostgresHandoffOutbox) -> None:
    """A fresh handoff writes a row and returns a packet naming a real ticket."""
    packet = _record(outbox, _content())

    assert packet.ticket_ref.startswith("T-20260618-")
    assert packet.customer.masked_id == "****1234"


@pytest.mark.integration
def test_a_repeated_turn_id_returns_the_same_ticket(outbox: PostgresHandoffOutbox) -> None:
    """A retried turn never mints a second ticket for one logical handoff."""
    first = _record(outbox, _content())

    second = _record(outbox, _content())

    assert second.ticket_ref == first.ticket_ref
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM handoff_outbox WHERE session_id = 's-1'")
        assert cur.fetchone()[0] == 1  # type: ignore[index]


@pytest.mark.integration
def test_a_different_turn_id_writes_a_second_row(outbox: PostgresHandoffOutbox) -> None:
    """Two genuinely different turns are two tickets, even for the same session."""
    first = _record(outbox, _content(), turn_id="t-1")

    second = _record(outbox, _content(), turn_id="t-2")

    assert second.ticket_ref != first.ticket_ref


@pytest.mark.integration
def test_a_replay_with_different_content_is_refused(outbox: PostgresHandoffOutbox) -> None:
    """A repeated (session_id, turn_id) with disagreeing content is never silently trusted."""
    _record(outbox, _content(trigger=HandoffTrigger.CUSTOMER_REQUEST))

    with pytest.raises(HandoffReplayMismatch):
        _record(outbox, _content(trigger=HandoffTrigger.CARD_LOSS))


@pytest.mark.integration
def test_a_replay_with_different_risk_is_refused(outbox: PostgresHandoffOutbox) -> None:
    """A field beyond the top-level scalars — risk evidence — is checked too, not just the ones
    most visible at a glance."""
    _record(outbox, _content())

    with pytest.raises(HandoffReplayMismatch):
        _record(
            outbox,
            _content(
                risk=RiskEvidence(score=0.9, interval_low=0.8, interval_high=0.95, base_rate=0.01)
            ),
        )


@pytest.mark.integration
def test_a_replay_with_different_reason_codes_is_refused(outbox: PostgresHandoffOutbox) -> None:
    """A repeating child-table part (reason codes) is compared too, not only scalar columns."""
    _record(outbox, _content(reason_codes=(ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,)))

    with pytest.raises(HandoffReplayMismatch):
        _record(outbox, _content(reason_codes=(ReasonCode.ESCALATE_FRAUD_CLAIM,)))


def _transaction_fact(ref: str) -> TransactionFact:
    return TransactionFact(
        ref=ref,
        occurred_on=_REFERENCE_DATE,
        merchant="A Shop",
        amount=Money(amount=Decimal("10.00"), currency="USD"),
        product=ProductLabel(name="Visa", last4="1234"),
        status=TransactionStatus.APPROVED,
    )


@pytest.mark.parametrize(
    ("baseline", "changed"),
    [
        (
            {"verified_facts": (_transaction_fact("tx-1"),)},
            {"verified_facts": (_transaction_fact("tx-2"),)},
        ),
        (
            {"actions": (ActionRecord(action="looked_up_transaction", result="found"),)},
            {"actions": (ActionRecord(action="looked_up_transaction", result="not_found"),)},
        ),
        (
            {"open_questions": (OpenQuestion(slot=Slot.REASON, attempts=1),)},
            {"open_questions": (OpenQuestion(slot=Slot.REASON, attempts=2),)},
        ),
        ({"existing_case_number": "D-1"}, {"existing_case_number": "D-2"}),
        (
            {"attempted_action": ActionRecord(action="create_dispute_case", result="tool_failure")},
            {"attempted_action": ActionRecord(action="create_dispute_case", result="timeout")},
        ),
        (
            {
                "sources": (
                    SourceRef(
                        section_id="filing-windows",
                        titles=(
                            LocalizedTitle(lang="es", text="A"),
                            LocalizedTitle(lang="pt", text="A"),
                            LocalizedTitle(lang="en", text="A"),
                        ),
                        corpus_version="2",
                    ),
                )
            },
            {
                "sources": (
                    SourceRef(
                        section_id="evidence-required",
                        titles=(
                            LocalizedTitle(lang="es", text="B"),
                            LocalizedTitle(lang="pt", text="B"),
                            LocalizedTitle(lang="en", text="B"),
                        ),
                        corpus_version="2",
                    ),
                )
            },
        ),
        ({"category": DisputeCategory.FRAUD_CLAIM}, {"category": DisputeCategory.WRONG_AMOUNT}),
    ],
    ids=[
        "verified_facts",
        "actions",
        "open_questions",
        "existing_case_number",
        "attempted_action",
        "sources",
        "category",
    ],
)
@pytest.mark.integration
def test_a_replay_mismatch_is_caught_for_every_persisted_field(
    outbox: PostgresHandoffOutbox, baseline: dict[str, object], changed: dict[str, object]
) -> None:
    """Every persisted, content-bearing field is part of the mismatch check, not only the ones
    most visible at a glance."""
    _record(outbox, _content(**baseline))

    with pytest.raises(HandoffReplayMismatch):
        _record(outbox, _content(**changed))


@pytest.mark.integration
def test_child_rows_persist_actions_questions_reasons_and_sources(
    outbox: PostgresHandoffOutbox,
) -> None:
    """Every bounded repeating part of the packet round-trips through its own child table."""
    source = SourceRef(
        section_id="filing-windows",
        titles=(
            LocalizedTitle(lang="es", text="Plazos"),
            LocalizedTitle(lang="pt", text="Prazos"),
            LocalizedTitle(lang="en", text="Windows"),
        ),
        corpus_version="2",
    )
    content = _content(
        actions=(ActionRecord(action="looked_up_transaction", result="found"),),
        open_questions=(OpenQuestion(slot=Slot.REASON, attempts=2),),
        reason_codes=(ReasonCode.ESCALATE_FRAUD_CLAIM, ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE),
        sources=(source,),
    )

    packet = _record(outbox, content)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT action, result FROM handoff_actions WHERE ticket_ref = %s ORDER BY ord",
            (packet.ticket_ref,),
        )
        assert cur.fetchall() == [("looked_up_transaction", "found")]
        cur.execute(
            "SELECT slot, attempts FROM handoff_open_questions WHERE ticket_ref = %s",
            (packet.ticket_ref,),
        )
        assert cur.fetchall() == [("reason", 2)]
        cur.execute(
            "SELECT reason_code FROM handoff_reason_codes WHERE ticket_ref = %s ORDER BY ord",
            (packet.ticket_ref,),
        )
        assert cur.fetchall() == [
            ("escalate_fraud_claim",),
            ("escalate_low_nlu_confidence",),
        ]
        cur.execute(
            "SELECT section_id, corpus_version FROM handoff_sources WHERE ticket_ref = %s",
            (packet.ticket_ref,),
        )
        assert cur.fetchall() == [("filing-windows", "2")]
        cur.execute(
            "SELECT customer_id, trace_id FROM handoff_outbox WHERE ticket_ref = %s",
            (packet.ticket_ref,),
        )
        assert cur.fetchone() == ("CLI-1234", "trace-1")


@pytest.mark.integration
def test_a_genuine_race_resolves_to_one_ticket(outbox: PostgresHandoffOutbox) -> None:
    """Two threads recording the same turn concurrently agree on exactly one ticket."""
    barrier = threading.Barrier(2)
    tickets: list[str] = []
    lock = threading.Lock()

    def _attempt() -> None:
        barrier.wait()
        packet = _record(outbox, _content())
        with lock:
            tickets.append(packet.ticket_ref)

    threads = [threading.Thread(target=_attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(tickets) == 2
    assert tickets[0] == tickets[1]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM handoff_outbox WHERE session_id = 's-1'")
        assert cur.fetchone()[0] == 1  # type: ignore[index]


@pytest.mark.integration
def test_a_ticket_collision_is_logged_not_silently_reclassified(
    outbox: PostgresHandoffOutbox, caplog: pytest.LogCaptureFixture
) -> None:
    """A UniqueViolation on a constraint other than (session_id, turn_id) is a real failure.

    Forces a genuine ``ticket_ref`` primary-key collision (rather than mocking psycopg's
    internals) by pinning the random suffix a *different* session's handoff already used, so the
    fresh insert this test makes hits the real constraint under test end to end.
    """
    _record(outbox, _content(), turn_id="other-turn")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT ticket_ref FROM handoff_outbox WHERE turn_id = 'other-turn'")
        row = cur.fetchone()
        assert row is not None
        colliding_suffix = row[0].rsplit("-", 1)[1]

    with (
        patch("secrets.token_hex", return_value=colliding_suffix.lower()),
        caplog.at_level("WARNING", logger="app.persistence.handoff_outbox"),
        pytest.raises(psycopg.errors.UniqueViolation),
    ):
        outbox.record(_content(), session_id="s-2", turn_id="t-1", trace_id="trace-1")

    assert any("handoff_outbox_write_failed" in record.message for record in caplog.records)


@pytest.mark.integration
def test_a_store_failure_logs_before_propagating(caplog: pytest.LogCaptureFixture) -> None:
    """A genuine store failure is logged, then still raised — never swallowed."""
    outbox = PostgresHandoffOutbox("postgresql://nobody:nowhere@localhost:1/does_not_exist")

    with (
        caplog.at_level("WARNING", logger="app.persistence.handoff_outbox"),
        pytest.raises(psycopg.Error),
    ):
        _record(outbox, _content())

    assert any("handoff_outbox_write_failed" in record.message for record in caplog.records)


@pytest.mark.integration
def test_a_row_with_no_stored_fingerprint_is_never_trusted_as_a_replay(
    outbox: PostgresHandoffOutbox,
) -> None:
    """A row written before migration 0009 has no fingerprint to verify against; it is refused
    the same way a genuine mismatch is, never silently trusted as an ordinary replay."""
    packet = _record(outbox, _content(), turn_id="legacy-turn")
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE handoff_outbox SET content_fingerprint = NULL WHERE ticket_ref = %s",
            (packet.ticket_ref,),
        )

    with pytest.raises(HandoffReplayMismatch):
        _record(outbox, _content(), turn_id="legacy-turn")
