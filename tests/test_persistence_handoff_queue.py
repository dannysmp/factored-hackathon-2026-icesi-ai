"""
Postgres Handoff Queue Tests
=============================

Component: ``app.persistence.handoff_queue``. Needs a real, migrated Postgres — every test is
marked ``integration``. Tickets are written through the real ``PostgresHandoffOutbox`` (never a
raw ``INSERT``), so a test fixture can never drift from what an actual handoff actually writes.
"""

from __future__ import annotations

# Standard libraries
import os
from datetime import UTC, date, datetime

# Third-party libraries
import psycopg
import pytest

# Local modules
from app.domain.calendar import DateOrigin, DomainCalendar
from app.domain.policy.models import DisputeCategory, ReasonCode
from app.persistence.handoff_outbox import HandoffContent, PostgresHandoffOutbox
from app.persistence.handoff_queue import PostgresHandoffQueue
from app.persistence.migrate import apply_migrations
from contracts.service_v1.console import QueueFilters, TicketStatus
from contracts.service_v1.handoff import HandoffTrigger

_CREATED_AT = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)


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
    with psycopg.connect(value, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE handoff_actions, handoff_open_questions, handoff_reason_codes, "
            "handoff_sources, handoff_outbox CASCADE"
        )
    return value


@pytest.fixture
def outbox(dsn: str) -> PostgresHandoffOutbox:
    return PostgresHandoffOutbox(dsn)


@pytest.fixture
def queue(dsn: str) -> PostgresHandoffQueue:
    return PostgresHandoffQueue(dsn, contact_days_priority=1, contact_days_default=2)


def _record(outbox: PostgresHandoffOutbox, content: HandoffContent, *, turn_id: str) -> None:
    outbox.record(content, session_id=f"s-{turn_id}", turn_id=turn_id, trace_id=f"trace-{turn_id}")


_TODAY = DomainCalendar(reference_date=date(2026, 6, 20), origin=DateOrigin.SETTING)


@pytest.mark.integration
def test_a_written_ticket_is_listed_with_computed_fields(
    outbox: PostgresHandoffOutbox, queue: PostgresHandoffQueue
) -> None:
    _record(
        outbox,
        _content(
            reference_date=date(2026, 6, 18),
            trigger=HandoffTrigger.CUSTOMER_REQUEST,
            language="pt",
            category=DisputeCategory.WRONG_AMOUNT,
        ),
        turn_id="t-1",
    )

    response = queue.list_tickets(QueueFilters(), calendar=_TODAY)

    assert response.reference_date == date(2026, 6, 20)
    assert len(response.items) == 1
    item = response.items[0]
    assert item.trigger is HandoffTrigger.CUSTOMER_REQUEST
    assert item.language == "pt"
    assert item.category is DisputeCategory.WRONG_AMOUNT
    assert item.status is TicketStatus.OPEN
    assert item.created_at == _CREATED_AT
    assert item.reference_date == date(2026, 6, 18)
    assert item.age_days == 2
    assert item.priority is False
    assert item.promised_contact_by == date(2026, 6, 20)  # +2 default days


@pytest.mark.integration
@pytest.mark.parametrize("trigger", [HandoffTrigger.FRAUD_REPORT, HandoffTrigger.CARD_LOSS])
def test_a_categoryless_priority_ticket_gets_the_priority_contact_window(
    outbox: PostgresHandoffOutbox, queue: PostgresHandoffQueue, trigger: HandoffTrigger
) -> None:
    """The exact case a customer can reach before any dispute category exists: a fraud report or
    a lost/stolen card report, still gets the fast (priority) contact window — computed from the
    trigger, never the (here, absent) category."""
    _record(
        outbox,
        _content(reference_date=date(2026, 6, 18), trigger=trigger, category=None),
        turn_id="t-1",
    )

    item = queue.list_tickets(QueueFilters(), calendar=_TODAY).items[0]

    assert item.category is None
    assert item.priority is True
    assert item.promised_contact_by == date(2026, 6, 19)  # +1 priority day


@pytest.mark.integration
def test_a_categorized_non_priority_ticket_still_gets_the_default_contact_window(
    outbox: PostgresHandoffOutbox, queue: PostgresHandoffQueue
) -> None:
    """The reverse proof: a ticket that DOES carry a category is still on the default window when
    its trigger is not a priority one — the category never enters this computation either way."""
    _record(
        outbox,
        _content(
            reference_date=date(2026, 6, 18),
            trigger=HandoffTrigger.AMOUNT_REVIEW,
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        ),
        turn_id="t-1",
    )

    item = queue.list_tickets(QueueFilters(), calendar=_TODAY).items[0]

    assert item.category is DisputeCategory.UNRECOGNIZED_CHARGE
    assert item.priority is False
    assert item.promised_contact_by == date(2026, 6, 20)  # +2 default days


@pytest.mark.integration
def test_priority_tickets_sort_before_non_priority_regardless_of_age(
    outbox: PostgresHandoffOutbox, queue: PostgresHandoffQueue
) -> None:
    _record(
        outbox,
        _content(reference_date=date(2026, 6, 10), trigger=HandoffTrigger.CUSTOMER_REQUEST),
        turn_id="old-nonpriority",
    )
    _record(
        outbox,
        _content(reference_date=date(2026, 6, 19), trigger=HandoffTrigger.FRAUD_REPORT),
        turn_id="new-priority",
    )

    items = queue.list_tickets(QueueFilters(), calendar=_TODAY).items

    assert [item.priority for item in items] == [True, False]


@pytest.mark.integration
def test_within_a_priority_tier_the_oldest_ticket_sorts_first(
    outbox: PostgresHandoffOutbox, queue: PostgresHandoffQueue
) -> None:
    _record(
        outbox,
        _content(reference_date=date(2026, 6, 19), trigger=HandoffTrigger.FRAUD_REPORT),
        turn_id="newer",
    )
    _record(
        outbox,
        _content(reference_date=date(2026, 6, 15), trigger=HandoffTrigger.CARD_LOSS),
        turn_id="older",
    )

    items = queue.list_tickets(QueueFilters(), calendar=_TODAY).items

    assert [item.age_days for item in items] == [5, 1]


@pytest.mark.integration
def test_a_language_filter_returns_only_matching_tickets(
    outbox: PostgresHandoffOutbox, queue: PostgresHandoffQueue
) -> None:
    _record(outbox, _content(language="es"), turn_id="es-ticket")
    _record(outbox, _content(language="pt"), turn_id="pt-ticket")

    response = queue.list_tickets(QueueFilters(language="pt"), calendar=_TODAY)

    assert [item.language for item in response.items] == ["pt"]


@pytest.mark.integration
def test_a_trigger_filter_returns_only_matching_tickets(
    outbox: PostgresHandoffOutbox, queue: PostgresHandoffQueue
) -> None:
    _record(outbox, _content(trigger=HandoffTrigger.FRAUD_REPORT), turn_id="fraud")
    _record(outbox, _content(trigger=HandoffTrigger.CARD_LOSS), turn_id="card-loss")

    response = queue.list_tickets(QueueFilters(trigger=HandoffTrigger.CARD_LOSS), calendar=_TODAY)

    assert [item.trigger for item in response.items] == [HandoffTrigger.CARD_LOSS]


@pytest.mark.integration
def test_no_tickets_returns_an_empty_queue(queue: PostgresHandoffQueue) -> None:
    response = queue.list_tickets(QueueFilters(), calendar=_TODAY)

    assert response.items == ()
