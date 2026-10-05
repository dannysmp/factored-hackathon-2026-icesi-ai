"""
Postgres Ticket Detail Tests
============================

Component: ``app.persistence.ticket_detail``. Needs a real, migrated Postgres — every test is
marked ``integration``. Tickets are written through the real ``PostgresHandoffOutbox`` (never a
raw ``INSERT``), so a test fixture can never drift from what an actual handoff actually writes.
The retriever is the real, committed corpus (``LexicalRetriever.from_corpus()``): the whole point
under test is re-hydration against the *actual* corpus, not a fake one.
"""

from __future__ import annotations

# Standard libraries
import os
from datetime import UTC, date, datetime
from decimal import Decimal

# Third-party libraries
import psycopg
import pytest

# Local modules
from app.conversation.handoff import HandoffContent
from app.domain.calendar import DateOrigin, DomainCalendar
from app.domain.policy.models import ReasonCode
from app.domain.policy.models import TransactionStatus as PolicyTransactionStatus
from app.persistence.dialogue_turn_log import PostgresDialogueTurnLog
from app.persistence.handoff_outbox import PostgresHandoffOutbox
from app.persistence.handoff_queue import PostgresHandoffQueue
from app.persistence.migrate import apply_migrations
from app.persistence.ticket_detail import PostgresTicketDetail, SourceUnavailable
from app.retrieval.lexical import LexicalRetriever
from contracts.service_v1.console import TimelineEntry
from contracts.service_v1.envelope import (  # The envelope-flavored shapes HandoffPacket expects
    Intent,
    LocalizedTitle,
    Money,
    ProductLabel,
    RiskEvidence,
    Slot,
    SourceRef,
    TransactionFact,
)
from contracts.service_v1.handoff import ActionRecord, HandoffTrigger, OpenQuestion

_ALL_LANGUAGE_TITLES = tuple(LocalizedTitle(lang=lang, text="Title") for lang in ("es", "pt", "en"))

_CREATED_AT = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)
_TODAY = DomainCalendar(reference_date=date(2026, 6, 20), origin=DateOrigin.SETTING)

# A section every language's copy of the real, committed corpus actually has — the point under
# test is re-hydration against the real corpus, not a fixture standing in for it.
_REAL_SECTION_ID = "overview"


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
            "handoff_sources, handoff_outbox, cases, transactions, products, customers, "
            "dialogue_turn_log CASCADE"
        )
        conn.commit()
    with psycopg.connect(value, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customers (customer_id, first_name, last_name, country, "
            "customer_status) VALUES ('CLI-1234', 'Ana', 'Gomez', 'México', 'Active')"
        )
        cur.execute(
            "INSERT INTO products (product_id, customer_id, product_type, last4, "
            "product_status) VALUES ('PRD-1', 'CLI-1234', 'Tarjeta de crédito', '4321', 'Active')"
        )
        cur.execute(
            "INSERT INTO transactions (transaction_id, customer_id, product_id, "
            "transaction_date, transaction_type, merchant_name, amount, currency, amount_usd, "
            "amount_usd_provenance, transaction_status) VALUES "
            "('TRX-1', 'CLI-1234', 'PRD-1', '2026-06-08 09:00:00', 'Purchase', 'A Merchant', "
            "100.00, 'USD', 100.00, 'reported', 'Approved')"
        )
    return value


@pytest.fixture
def outbox(dsn: str) -> PostgresHandoffOutbox:
    return PostgresHandoffOutbox(dsn)


@pytest.fixture
def reader(dsn: str) -> PostgresTicketDetail:
    queue = PostgresHandoffQueue(dsn, contact_days_priority=1, contact_days_default=2)
    turn_log = PostgresDialogueTurnLog(dsn)
    retriever = LexicalRetriever.from_corpus()
    return PostgresTicketDetail(dsn, retriever=retriever, queue=queue, turn_log=turn_log)


def _record(outbox: PostgresHandoffOutbox, content: HandoffContent, *, turn_id: str) -> str:
    packet = outbox.record(
        content, session_id=f"s-{turn_id}", turn_id=turn_id, trace_id=f"trace-{turn_id}"
    )
    return packet.ticket_ref


def _record_timeline_entry(dsn: str, *, trace_id: str, turn_id: str) -> None:
    PostgresDialogueTurnLog(dsn).record(
        TimelineEntry(
            occurred_at=_CREATED_AT,
            trace_id=trace_id,
            turn_id=turn_id,
            intent=Intent.HANDOFF,
            state_before="started",
            state_after="handed_off",
            render_mode="template",
        ),
        session_id=f"s-{turn_id}",
    )


@pytest.mark.integration
def test_answers_none_for_an_unknown_ticket(reader: PostgresTicketDetail) -> None:
    assert reader.get_ticket_detail("T-20260618-DEADBEEF", calendar=_TODAY) is None


@pytest.mark.integration
def test_the_queue_row_and_the_packet_agree_on_the_bare_minimum(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail
) -> None:
    """``TicketDetail``'s own validator already enforces this; a mismatch would fail construction
    before this test's own assertions even run."""
    ticket_ref = _record(outbox, _content(trigger=HandoffTrigger.FRAUD_REPORT), turn_id="t-1")

    detail = reader.get_ticket_detail(ticket_ref, calendar=_TODAY)

    assert detail is not None
    assert detail.item.ticket_ref == detail.packet.ticket_ref == ticket_ref
    assert detail.item.trigger is detail.packet.trigger is HandoffTrigger.FRAUD_REPORT


@pytest.mark.integration
def test_re_hydrates_the_verified_transaction_live(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail
) -> None:
    fact = TransactionFact(
        ref="TRX-1",
        occurred_on=date(2026, 6, 8),
        merchant="A Merchant",
        amount=Money(amount=Decimal("100.00"), currency="USD"),
        product=ProductLabel(name="Tarjeta de crédito", last4="4321"),
        status=PolicyTransactionStatus.APPROVED,
    )
    ticket_ref = _record(outbox, _content(verified_facts=(fact,)), turn_id="t-1")

    detail = reader.get_ticket_detail(ticket_ref, calendar=_TODAY)

    assert detail is not None
    assert len(detail.packet.verified_facts) == 1
    assert detail.packet.verified_facts[0].ref == "TRX-1"
    assert detail.packet.verified_facts[0].merchant == "A Merchant"


@pytest.mark.integration
def test_a_merchant_name_over_the_contracts_bound_is_truncated_not_a_crash(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail, dsn: str
) -> None:
    """The live re-hydration reads the store's own row fresh, so a merchant name over the
    contract's 80-character bound (the store's own column allows up to 150) must be clamped here
    too, the same way app.persistence.reads already is — this module reads the same table into
    the same-named, separately-defined envelope.TransactionFact contract."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO transactions (transaction_id, customer_id, product_id, "
            "transaction_date, transaction_type, merchant_name, amount, currency, amount_usd, "
            "amount_usd_provenance, transaction_status) VALUES "
            "('TRX-LONG', 'CLI-1234', 'PRD-1', '2026-06-08 09:00:00', 'Purchase', %s, 100.00, "
            "'USD', 100.00, 'reported', 'Approved')",
            ("A" * 150,),
        )
    fact = TransactionFact(
        ref="TRX-LONG",
        occurred_on=date(2026, 6, 8),
        merchant="A Merchant",  # the packet's own frozen value; re-hydration reads the store live
        amount=Money(amount=Decimal("100.00"), currency="USD"),
        product=ProductLabel(name="Tarjeta de crédito", last4="4321"),
        status=PolicyTransactionStatus.APPROVED,
    )
    ticket_ref = _record(outbox, _content(verified_facts=(fact,)), turn_id="t-long")

    detail = reader.get_ticket_detail(ticket_ref, calendar=_TODAY)

    assert detail is not None
    assert len(detail.packet.verified_facts) == 1
    assert detail.packet.verified_facts[0].merchant == "A" * 80


@pytest.mark.integration
def test_a_transaction_that_no_longer_resolves_yields_no_fact_rather_than_failing(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail, dsn: str
) -> None:
    """Unlike a source, a missing transaction has no customer-visible presentation obligation: the
    packet still shows what was actually verified (its own frozen fields), just without the live
    re-fetch succeeding."""
    fact = TransactionFact(
        ref="TRX-1",
        occurred_on=date(2026, 6, 8),
        merchant="A Merchant",
        amount=Money(amount=Decimal("100.00"), currency="USD"),
        product=ProductLabel(name="Tarjeta de crédito", last4="4321"),
        status=PolicyTransactionStatus.APPROVED,
    )
    ticket_ref = _record(outbox, _content(verified_facts=(fact,)), turn_id="t-1")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM transactions WHERE transaction_id = 'TRX-1'")

    detail = reader.get_ticket_detail(ticket_ref, calendar=_TODAY)

    assert detail is not None
    assert detail.packet.verified_facts == ()


@pytest.mark.integration
def test_no_verified_transaction_ref_yields_an_empty_tuple(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail
) -> None:
    ticket_ref = _record(outbox, _content(), turn_id="t-1")

    detail = reader.get_ticket_detail(ticket_ref, calendar=_TODAY)

    assert detail is not None
    assert detail.packet.verified_facts == ()


@pytest.mark.integration
def test_a_sources_title_is_re_hydrated_but_its_stored_corpus_version_is_kept(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail
) -> None:
    """The stored ``corpus_version`` is the historical fact of what actually grounded the
    decision; it must survive even though the live corpus, asked today, may report a different
    one — proven here by deliberately storing a version that disagrees with the real corpus."""
    source = SourceRef(
        section_id=_REAL_SECTION_ID,
        titles=_ALL_LANGUAGE_TITLES,  # not compared against; handoff_outbox never stores titles
        corpus_version="ancient-1",
    )
    ticket_ref = _record(outbox, _content(sources=(source,)), turn_id="t-1")

    detail = reader.get_ticket_detail(ticket_ref, calendar=_TODAY)

    assert detail is not None
    reconstructed = detail.packet.evidence.sources[0]
    assert reconstructed.section_id == _REAL_SECTION_ID
    assert reconstructed.corpus_version == "ancient-1"  # kept, not overwritten
    assert reconstructed.title_for("es")  # re-hydrated live: real, non-empty title


@pytest.mark.integration
def test_a_source_that_no_longer_resolves_fails_the_whole_read(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail
) -> None:
    source = SourceRef(
        section_id="not-a-real-section-xyz", titles=_ALL_LANGUAGE_TITLES, corpus_version="1"
    )
    ticket_ref = _record(outbox, _content(sources=(source,)), turn_id="t-1")

    with pytest.raises(SourceUnavailable):
        reader.get_ticket_detail(ticket_ref, calendar=_TODAY)


@pytest.mark.integration
def test_actions_open_questions_and_reason_codes_round_trip_in_order(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail
) -> None:
    ticket_ref = _record(
        outbox,
        _content(
            actions=(
                ActionRecord(action="list_transactions", result="ok"),
                ActionRecord(action="evaluate_dispute", result="ineligible"),
            ),
            open_questions=(OpenQuestion(slot=Slot.REASON, attempts=2),),
            reason_codes=(ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,),
        ),
        turn_id="t-1",
    )

    detail = reader.get_ticket_detail(ticket_ref, calendar=_TODAY)

    assert detail is not None
    assert [a.action for a in detail.packet.actions] == ["list_transactions", "evaluate_dispute"]
    assert detail.packet.open_questions == (OpenQuestion(slot=Slot.REASON, attempts=2),)
    assert detail.packet.evidence.reason_codes == (ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,)


@pytest.mark.integration
def test_risk_evidence_round_trips_when_present(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail
) -> None:
    risk = RiskEvidence(
        score=0.7, interval_low=0.6, interval_high=0.8, base_rate=0.1, threshold=0.75
    )
    ticket_ref = _record(
        outbox,
        _content(trigger=HandoffTrigger.RISK_SCORE, risk=risk),
        turn_id="t-1",
    )

    detail = reader.get_ticket_detail(ticket_ref, calendar=_TODAY)

    assert detail is not None
    assert detail.packet.evidence.risk == risk


@pytest.mark.integration
def test_no_risk_evidence_stays_none(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail
) -> None:
    ticket_ref = _record(outbox, _content(), turn_id="t-1")

    detail = reader.get_ticket_detail(ticket_ref, calendar=_TODAY)

    assert detail is not None
    assert detail.packet.evidence.risk is None


@pytest.mark.integration
def test_the_customer_label_is_read_back_verbatim_never_re_masked(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail
) -> None:
    """The outbox stores an already-masked id; a reconstruction that re-masked it (treating the
    stored value as a raw customer id) would corrupt it into an unrecognizable string."""
    ticket_ref = _record(outbox, _content(first_name="Ana", customer_id="CLI-1234"), turn_id="t-1")
    original = outbox.record(
        _content(first_name="Ana", customer_id="CLI-1234"),
        session_id="s-t-1",
        turn_id="t-1",
        trace_id="trace-t-1",
    )  # the same (session_id, turn_id): the idempotent replay, not a second ticket

    detail = reader.get_ticket_detail(ticket_ref, calendar=_TODAY)

    assert detail is not None
    assert detail.packet.customer == original.customer
    assert detail.packet.customer.first_name == "Ana"


@pytest.mark.integration
def test_the_timeline_comes_from_the_conversation_the_ticket_was_recorded_under(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail, dsn: str
) -> None:
    ticket_ref = _record(outbox, _content(), turn_id="t-1")
    _record_timeline_entry(dsn, trace_id="trace-t-1", turn_id="t-1")

    detail = reader.get_ticket_detail(ticket_ref, calendar=_TODAY)

    assert detail is not None
    assert len(detail.timeline) == 1
    assert detail.timeline[0].intent is Intent.HANDOFF
    assert detail.timeline[0].turn_id == "t-1"


@pytest.mark.integration
def test_no_timeline_entries_yields_an_empty_tuple(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail
) -> None:
    ticket_ref = _record(outbox, _content(), turn_id="t-1")

    detail = reader.get_ticket_detail(ticket_ref, calendar=_TODAY)

    assert detail is not None
    assert detail.timeline == ()


@pytest.mark.integration
def test_a_ticket_that_has_left_the_open_queue_still_answers(
    outbox: PostgresHandoffOutbox, reader: PostgresTicketDetail, dsn: str
) -> None:
    """An already-selected ticket must still resolve even after it leaves the open
    queue (resolved, rejected) — this reader must not silently mirror ``list_tickets``' own
    exclusion of closed statuses."""
    ticket_ref = _record(outbox, _content(), turn_id="t-1")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE handoff_outbox SET status = 'resolved' WHERE ticket_ref = %s", (ticket_ref,)
        )

    detail = reader.get_ticket_detail(ticket_ref, calendar=_TODAY)

    assert detail is not None
