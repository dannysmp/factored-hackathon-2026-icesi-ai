"""
Case Creation Tests
====================

Component: ``app.persistence.reads.PostgresToolPort.create_dispute_case``. Needs a real, migrated
Postgres — the constraints under test (migration 0004's reason-code check and the partial unique
index), the race-losing paths and the genuine two-thread races only exist at the store; marked
``integration``, skipped when ``DATABASE_URL`` is not set. Mirrors
``tests/test_persistence_reads.py``'s fixture style: a self-contained seed, not shared through a
``conftest.py``.

The create tool never evaluates policy: every test hands it an already-decided
``PolicyDecision`` built by ``_decision`` below, exactly as the controller would after evaluating
one itself, and checks only the permission invariants the tool enforces on top of it.

The two genuine-race tests synchronize two real threads with a ``threading.Barrier`` around a
patched read (``_find_by_idempotency_key`` or ``_open_case_number_for``), so both threads observe
"no conflict yet" before either inserts — the only way to make the store's own unique constraints,
not the tool's proactive pre-checks, be what actually resolves the race.
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, date, datetime
from unittest.mock import patch

import psycopg
import psycopg.errors
import pytest

from app.domain.policy.loader import load_policy
from app.domain.policy.models import DisputeCategory, Outcome, PolicyDecision, ReasonCode
from app.persistence.audit import PostgresAuditSink
from app.persistence.migrate import apply_migrations
from app.persistence.reads import PostgresToolPort
from contracts.service_v1.audit import AuditRecord, AuditSink
from contracts.service_v1.cases import Lang
from contracts.service_v1.tools import (
    CreateDisputeCaseRequest,
    CreateDisputeCaseResult,
    Tool,
    ToolFailure,
)

DOMAIN_DATE = "2026-06-18"
NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
POLICY_VERSION = load_policy().version


def _case_numbers(port: PostgresToolPort) -> list[str]:
    """The session's own case numbers, asserting the call itself did not fail at the store."""
    cases = port.list_dispute_cases()
    assert not isinstance(cases, ToolFailure)
    return [case.case_number for case in cases]


class _RecordingSink:
    """A real ``AuditSink`` that also remembers every record, for asserting on the trail."""

    def __init__(self, dsn: str) -> None:
        self._sink = PostgresAuditSink(dsn)
        self.records: list[AuditRecord] = []

    def record(self, entry: AuditRecord) -> None:
        self._sink.record(entry)
        self.records.append(entry)


class _FailingSink:
    """An ``AuditSink`` that always refuses to write, for the fail-closed test."""

    def record(self, entry: AuditRecord) -> None:
        raise RuntimeError("audit store is down")


def _port(
    dsn: str,
    audit: AuditSink,
    *,
    customer_id: str,
    session_id: str = "SESSION-1",
    language: Lang = "en",
    case_create_session_cap: int = 3,
) -> PostgresToolPort:
    return PostgresToolPort(
        dsn,
        audit,
        load_policy(),
        customer_id=customer_id,
        session_id=session_id,
        trace_id="TRACE-1",
        domain_date=date.fromisoformat(DOMAIN_DATE),
        now=lambda: NOW,
        language=language,
        case_create_session_cap=case_create_session_cap,
    )


def _decision(
    *,
    transaction_ref: str,
    category: DisputeCategory = DisputeCategory.UNRECOGNIZED_CHARGE,
) -> PolicyDecision:
    """An already-eligible, confirmation-required decision, exactly as the controller would hold."""
    return PolicyDecision(
        outcome=Outcome.ELIGIBLE,
        reason_code=ReasonCode.ELIGIBLE,
        policy_version=POLICY_VERSION,
        requires_confirmation=True,
        facts=(),
        triggers=(),
        transaction_ref=transaction_ref,
        category=category,
    )


_DEFAULT_DECISION = object()


def _request(
    *,
    transaction_ref: str = "TRX-A1",
    category: DisputeCategory = DisputeCategory.UNRECOGNIZED_CHARGE,
    confirmed: bool = True,
    idempotency_key: str = "IDEMP-1",
    decision: PolicyDecision | None = _DEFAULT_DECISION,  # type: ignore[assignment]
) -> CreateDisputeCaseRequest:
    """``decision`` defaults to a matching, eligible one; pass ``None`` explicitly for the
    ``decision_missing`` case, distinct from simply not overriding the default."""
    if decision is _DEFAULT_DECISION:
        decision = _decision(transaction_ref=transaction_ref, category=category)
    return CreateDisputeCaseRequest(
        transaction_ref=transaction_ref,
        category=category,
        confirmed=confirmed,
        idempotency_key=idempotency_key,
        decision=decision,
    )


def _insert_case_row(dsn: str, **overrides: str) -> None:
    """A case row inserted directly, bypassing the tool — for seeding a competing row out of
    band, before or during a call the test makes through the port."""
    values = {
        "case_number": "CASE-DIRECT",
        "customer_id": "CLI-A",
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
            {**values, "policy_version": POLICY_VERSION},
        )


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(value)
    # TRUNCATE on audit_log is refused at the store (migration 0003), including for this reset:
    # the session's own replication role is switched off for it, since a trigger created without
    # ENABLE REPLICA or ENABLE ALWAYS does not fire under 'replica'.
    with psycopg.connect(value) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute("TRUNCATE TABLE cases, transactions, products, customers, audit_log CASCADE")
        conn.commit()
    with psycopg.connect(value, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customers (customer_id, first_name, last_name, country, "
            "customer_status) VALUES ('CLI-A', 'First', 'Last', 'México', 'Active')"
        )
        cur.execute(
            "INSERT INTO products (product_id, customer_id, product_type, last4, "
            "product_status) VALUES ('PRD-A', 'CLI-A', 'Cuenta Corriente', '1234', 'Active')"
        )
        cur.executemany(
            "INSERT INTO transactions (transaction_id, customer_id, product_id, "
            "transaction_date, transaction_type, merchant_name, amount, currency, amount_usd, "
            "amount_usd_provenance, transaction_status) VALUES "
            "(%s, 'CLI-A', 'PRD-A', '2026-06-08 09:00:00', 'Purchase', 'A Merchant', 100.00, "
            "'USD', 100.00, 'reported', 'Approved')",
            [
                ("TRX-A1",),  # No case yet: the happy-path and idempotency fixture.
                ("TRX-A2",),  # Already has an open case: the duplicate-open-case fixture.
                ("TRX-A3",),  # No case yet: a second, distinct transaction for the cap test.
            ],
        )
        cur.execute(
            "INSERT INTO cases (case_number, customer_id, transaction_id, session_id, "
            "idempotency_key, status, category, amount, currency, amount_provenance, "
            "domain_date, expected_first_response_date, created_at_utc, policy_version, "
            "reason_code, language) VALUES "
            "('CASE-A2-OPEN', 'CLI-A', 'TRX-A2', 'SESSION-0', 'IDEMP-A2', 'Open', "
            "'unrecognized_charge', 100.00, 'USD', 'reported', '2026-06-01', '2026-10-01', "
            "'2026-06-01 09:00:00+00', '2', 'eligible', 'en')"
        )
    return value


# -----------------------------------------------------------------------------
# Happy path
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_a_confirmed_eligible_filing_creates_a_case_and_reads_back_correctly(dsn: str) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    result = port.create_dispute_case(_request(transaction_ref="TRX-A1"))

    assert isinstance(result, CreateDisputeCaseResult)
    assert result.created is True
    assert result.case_number is not None
    assert sink.records[-1].action.value == "case_created"

    record = port.get_case(result.case_number)
    assert record is not None
    assert not isinstance(record, ToolFailure)
    assert record.status.value == "Open"
    assert record.transaction_ref == "TRX-A1"
    assert record.category.value == "unrecognized_charge"
    assert record.amount.money is not None
    assert record.amount.money.amount == 100
    assert record.amount.money.currency == "USD"
    assert record.domain_date.isoformat() == DOMAIN_DATE
    # unrecognized_charge's first_response_days is 3 (policy/dispute_policy_v1.yaml); calendar
    # days are never adjusted for weekends (app.domain.policy.engine.expected_first_response).
    assert record.expected_first_response_date == date(2026, 6, 21)
    assert record.created_at_utc == NOW
    assert record.policy_version == POLICY_VERSION
    assert record.language == "en"


# -----------------------------------------------------------------------------
# Confirmation and decision
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("confirmed", [False])
@pytest.mark.integration
def test_an_unconfirmed_filing_is_refused_and_creates_nothing(dsn: str, confirmed: bool) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    result = port.create_dispute_case(_request(transaction_ref="TRX-A1", confirmed=confirmed))

    assert isinstance(result, CreateDisputeCaseResult)
    assert result.created is False
    assert result.refusal is not None
    assert result.refusal.value == "confirmation_required"
    assert sink.records[-1].action.value == "case_creation_refused"
    assert _case_numbers(port) == ["CASE-A2-OPEN"]


@pytest.mark.integration
def test_a_filing_with_no_decision_is_refused_decision_missing(dsn: str) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    result = port.create_dispute_case(_request(transaction_ref="TRX-A1", decision=None))

    assert isinstance(result, CreateDisputeCaseResult)
    assert result.created is False
    assert result.refusal is not None
    assert result.refusal.value == "decision_missing"
    assert _case_numbers(port) == ["CASE-A2-OPEN"]


# -----------------------------------------------------------------------------
# The case filed is always the one the customer saw
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filed_ref", "filed_category", "decided_ref", "decided_category"),
    [
        ("TRX-A1", "unrecognized_charge", "TRX-A2", "unrecognized_charge"),
        ("TRX-A1", "unrecognized_charge", "TRX-A1", "duplicate_charge"),
    ],
    ids=["transaction-mismatch", "category-mismatch"],
)
@pytest.mark.integration
def test_a_filing_that_does_not_match_the_decision_is_refused_confirmation_mismatch(
    dsn: str, filed_ref: str, filed_category: str, decided_ref: str, decided_category: str
) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")
    decision = _decision(transaction_ref=decided_ref, category=DisputeCategory(decided_category))

    result = port.create_dispute_case(
        _request(
            transaction_ref=filed_ref,
            category=DisputeCategory(filed_category),
            decision=decision,
        )
    )

    assert isinstance(result, CreateDisputeCaseResult)
    assert result.created is False
    assert result.refusal is not None
    assert result.refusal.value == "confirmation_mismatch"
    assert _case_numbers(port) == ["CASE-A2-OPEN"]


# -----------------------------------------------------------------------------
# Idempotency replay — sequential, out-of-band, and a genuine race
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_the_same_key_and_payload_filed_twice_replays_the_same_case(dsn: str) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")
    request = _request(transaction_ref="TRX-A1", idempotency_key="IDEMP-SAME")

    first = port.create_dispute_case(request)
    second = port.create_dispute_case(request)

    assert isinstance(first, CreateDisputeCaseResult)
    assert isinstance(second, CreateDisputeCaseResult)
    assert first.created is True
    assert second.created is True
    assert first.case_number == second.case_number
    # The freshly filed case (created_at_utc = NOW) sorts before the seeded, older CASE-A2-OPEN.
    assert _case_numbers(port) == [first.case_number, "CASE-A2-OPEN"]
    # Exactly one case_created record; the replay gets its own, distinct audit action, so a trace
    # built from audit records alone still shows both filing calls the customer actually made.
    actions = [r.action.value for r in sink.records]
    assert actions.count("case_created") == 1
    assert actions.count("case_creation_replayed") == 1


@pytest.mark.integration
def test_a_key_already_taken_before_the_call_replays_via_the_proactive_check(dsn: str) -> None:
    """The competing row exists before ``create_dispute_case`` is even called, so this exercises
    the proactive ``_find_by_idempotency_key`` pre-check, not the store's ``UniqueViolation``
    handler — that handler needs a call already in flight when the competing row lands, which
    only a genuine concurrent race (below) can force."""
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")
    request = _request(transaction_ref="TRX-A1", idempotency_key="IDEMP-OUT-OF-BAND")
    _insert_case_row(
        dsn,
        case_number="CASE-OUT-OF-BAND-WINNER",
        idempotency_key="IDEMP-OUT-OF-BAND",
    )

    result = port.create_dispute_case(request)

    assert isinstance(result, CreateDisputeCaseResult)
    assert result.created is True
    assert result.case_number == "CASE-OUT-OF-BAND-WINNER"


@pytest.mark.integration
def test_a_genuine_concurrent_race_on_the_same_idempotency_key_is_resolved_by_the_store(
    dsn: str,
) -> None:
    """Two real threads, synchronized so both pass the proactive idempotency pre-check before
    either inserts: the store's own unique constraint (``cases_customer_idempotency_key_unique``,
    migration 0001) is what actually resolves the race, exercised through
    ``PostgresToolPort``'s own ``UniqueViolation``-handling branch in ``_insert_case``, not
    simulated by inserting a row out of band."""
    barrier = threading.Barrier(2)
    waited = threading.local()
    original = PostgresToolPort._find_by_idempotency_key

    def _synced(self: PostgresToolPort, idempotency_key: str) -> object:
        result = original(self, idempotency_key)
        # Only each thread's first call (the proactive pre-check) synchronizes; the loser's
        # second call (the post-violation recheck) skips the wait — nothing else calls it in
        # that same round, so waiting there would only stall until the timeout for no reason.
        if not getattr(waited, "done", False):
            waited.done = True
            barrier.wait(timeout=5)
        return result

    request = _request(transaction_ref="TRX-A1", idempotency_key="IDEMP-TRUE-RACE")
    results: list[CreateDisputeCaseResult | ToolFailure | None] = [None, None]
    sinks = [_RecordingSink(dsn), _RecordingSink(dsn)]

    def _call(index: int) -> None:
        port = _port(dsn, sinks[index], customer_id="CLI-A", session_id=f"SESSION-RACE-{index}")
        results[index] = port.create_dispute_case(request)

    with patch.object(PostgresToolPort, "_find_by_idempotency_key", _synced):
        threads = [threading.Thread(target=_call, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

    assert all(isinstance(r, CreateDisputeCaseResult) for r in results)
    created = [r for r in results if isinstance(r, CreateDisputeCaseResult)]
    assert all(r.created for r in created)
    assert created[0].case_number == created[1].case_number
    # One winner (case_created) and one loser (case_creation_replayed) — never two case_created.
    all_actions = [r.action.value for sink in sinks for r in sink.records]
    assert all_actions.count("case_created") == 1
    assert all_actions.count("case_creation_replayed") == 1


@pytest.mark.integration
def test_the_idempotency_race_recheck_finding_nothing_fails_closed(dsn: str) -> None:
    """Defensive only: the recheck after a lost idempotency race normally finds the winner's row
    (proven above). If it ever found nothing — an unreachable window in practice, since the
    ``UniqueViolation`` itself guarantees a matching row exists — the call must fail closed
    rather than silently creating a second case for the same key."""
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")
    # The competing row is filed against TRX-A3 (no open case), while this call targets TRX-A1
    # (also no open case): the idempotency constraint is scoped only by (customer_id,
    # idempotency_key), so the two rows still collide on insert, but the mismatched transaction
    # keeps the proactive duplicate-open-case check from short-circuiting before that insert.
    request = _request(transaction_ref="TRX-A1", idempotency_key="IDEMP-FALLTHROUGH")
    _insert_case_row(
        dsn,
        case_number="CASE-FALLTHROUGH-WINNER",
        transaction_id="TRX-A3",
        idempotency_key="IDEMP-FALLTHROUGH",
    )

    with patch.object(PostgresToolPort, "_find_by_idempotency_key", return_value=None):
        result = port.create_dispute_case(request)

    assert isinstance(result, ToolFailure)
    assert result.tool == Tool.CREATE_DISPUTE_CASE
    assert result.cause == "error"


@pytest.mark.integration
def test_the_open_case_lookup_failing_during_the_race_handler_fails_closed(dsn: str) -> None:
    """Defensive only: if the lookup for the existing case number itself fails while handling a
    lost duplicate-open-case race, the call fails closed as a store error — never an unhandled
    exception, and never a refusal missing the field the contract requires it to carry."""
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")
    request = _request(transaction_ref="TRX-A3", idempotency_key="IDEMP-OPEN-LOOKUP-FAILS")
    # A different key, so this collides only on the open-case constraint, not the idempotency one.
    _insert_case_row(
        dsn,
        case_number="CASE-OPEN-RACE-WINNER",
        transaction_id="TRX-A3",
        idempotency_key="IDEMP-OTHER-KEY",
    )

    with patch.object(
        PostgresToolPort,
        "_open_case_number_for",
        side_effect=[None, psycopg.OperationalError("connection lost")],
    ):
        result = port.create_dispute_case(request)

    assert isinstance(result, ToolFailure)
    assert result.tool == Tool.CREATE_DISPUTE_CASE
    assert result.cause == "error"


# -----------------------------------------------------------------------------
# A reused key with a different payload, and a duplicate open case
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_the_same_key_with_a_different_payload_is_refused_idempotency_conflict(dsn: str) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")
    first = port.create_dispute_case(
        _request(transaction_ref="TRX-A1", idempotency_key="IDEMP-REUSED")
    )
    assert isinstance(first, CreateDisputeCaseResult)
    assert first.case_number is not None

    result = port.create_dispute_case(
        _request(
            transaction_ref="TRX-A1",
            category=DisputeCategory.DUPLICATE_CHARGE,
            idempotency_key="IDEMP-REUSED",
            decision=_decision(transaction_ref="TRX-A1", category=DisputeCategory.DUPLICATE_CHARGE),
        )
    )

    assert isinstance(result, CreateDisputeCaseResult)
    assert result.created is False
    assert result.refusal is not None
    assert result.refusal.value == "idempotency_conflict"
    assert set(_case_numbers(port)) == {first.case_number, "CASE-A2-OPEN"}


@pytest.mark.integration
def test_a_new_key_for_a_transaction_with_an_open_case_is_refused_duplicate_open_case(
    dsn: str,
) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    result = port.create_dispute_case(
        _request(transaction_ref="TRX-A2", idempotency_key="IDEMP-NEW")
    )

    assert isinstance(result, CreateDisputeCaseResult)
    assert result.created is False
    assert result.refusal is not None
    assert result.refusal.value == "duplicate_open_case"
    assert result.existing_case_number == "CASE-A2-OPEN"


@pytest.mark.integration
def test_a_genuine_concurrent_race_for_the_same_transaction_is_resolved_by_the_store(
    dsn: str,
) -> None:
    """Two real threads targeting a transaction with no open case yet, synchronized so both pass
    the proactive open-case pre-check before either inserts: the partial unique index
    (``cases_transaction_id_open_unique``, migration 0004) is what actually resolves the race,
    exercised through ``_insert_case``'s ``UniqueViolation`` handler for that constraint."""
    barrier = threading.Barrier(2)
    waited = threading.local()
    original = PostgresToolPort._open_case_number_for

    def _synced(self: PostgresToolPort, transaction_ref: str) -> object:
        result = original(self, transaction_ref)
        # Only each thread's first call (the proactive pre-check) synchronizes; the loser's
        # second call (the post-violation lookup) skips the wait — nothing else calls it in
        # that same round, so waiting there would only stall until the timeout for no reason.
        if not getattr(waited, "done", False):
            waited.done = True
            barrier.wait(timeout=5)
        return result

    results: list[CreateDisputeCaseResult | ToolFailure | None] = [None, None]

    def _call(index: int) -> None:
        sink = _RecordingSink(dsn)
        port = _port(dsn, sink, customer_id="CLI-A", session_id=f"SESSION-OPEN-RACE-{index}")
        request = _request(transaction_ref="TRX-A3", idempotency_key=f"IDEMP-OPEN-RACE-{index}")
        results[index] = port.create_dispute_case(request)

    with patch.object(PostgresToolPort, "_open_case_number_for", _synced):
        threads = [threading.Thread(target=_call, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

    outcomes = [r for r in results if isinstance(r, CreateDisputeCaseResult)]
    assert len(outcomes) == 2
    created = [r for r in outcomes if r.created]
    refused = [r for r in outcomes if not r.created]
    assert len(created) == 1
    assert len(refused) == 1
    assert refused[0].refusal is not None
    assert refused[0].refusal.value == "duplicate_open_case"
    assert refused[0].existing_case_number == created[0].case_number


# -----------------------------------------------------------------------------
# Check-order: duplicate_open_case is checked before the session cap
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_duplicate_open_case_takes_priority_over_the_session_cap(dsn: str) -> None:
    """When a call would trip both invariants, the customer hears about the specific transaction
    that already has a case, not a generic cap message that tells them nothing about which one."""
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A", case_create_session_cap=1)
    first = port.create_dispute_case(
        _request(transaction_ref="TRX-A1", idempotency_key="IDEMP-PRIORITY-1")
    )
    assert isinstance(first, CreateDisputeCaseResult)
    assert first.created is True  # The session is now at its cap of 1.

    result = port.create_dispute_case(
        _request(transaction_ref="TRX-A2", idempotency_key="IDEMP-PRIORITY-2")
    )

    assert isinstance(result, CreateDisputeCaseResult)
    assert result.created is False
    assert result.refusal is not None
    assert result.refusal.value == "duplicate_open_case"
    assert result.existing_case_number == "CASE-A2-OPEN"


# -----------------------------------------------------------------------------
# The session cap
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_a_session_that_reaches_its_filing_cap_is_refused(dsn: str) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A", case_create_session_cap=1)
    first = port.create_dispute_case(
        _request(transaction_ref="TRX-A1", idempotency_key="IDEMP-CAP-1")
    )
    assert isinstance(first, CreateDisputeCaseResult)
    assert first.created is True

    second = port.create_dispute_case(
        _request(transaction_ref="TRX-A3", idempotency_key="IDEMP-CAP-2")
    )

    assert isinstance(second, CreateDisputeCaseResult)
    assert second.created is False
    assert second.refusal is not None
    assert second.refusal.value == "session_cap_reached"


# -----------------------------------------------------------------------------
# Fail closed on the audit write
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_a_filing_that_cannot_be_audited_creates_no_case(dsn: str) -> None:
    port = _port(dsn, _FailingSink(), customer_id="CLI-A")

    with pytest.raises(RuntimeError, match="audit store is down"):
        port.create_dispute_case(_request(transaction_ref="TRX-A1"))

    recording_port = _port(dsn, _RecordingSink(dsn), customer_id="CLI-A")
    assert _case_numbers(recording_port) == ["CASE-A2-OPEN"]


# -----------------------------------------------------------------------------
# Migration 0004: the store-level constraints directly
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_the_reason_code_check_constraint_rejects_a_value_outside_the_closed_set(
    dsn: str,
) -> None:
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        psycopg.connect(dsn, autocommit=True) as conn,
        conn.cursor() as cur,
    ):
        cur.execute(
            "INSERT INTO cases (case_number, customer_id, transaction_id, session_id, "
            "idempotency_key, status, category, amount, currency, amount_provenance, "
            "domain_date, expected_first_response_date, created_at_utc, policy_version, "
            "reason_code, language) VALUES "
            "('CASE-BAD-REASON', 'CLI-A', 'TRX-A1', 'SESSION-1', 'IDEMP-BAD', 'Open', "
            "'unrecognized_charge', 100.00, 'USD', 'reported', '2026-06-01', '2026-10-01', "
            "'2026-06-01 09:00:00+00', '2', 'not_a_real_reason_code', 'en')"
        )


@pytest.mark.integration
def test_the_partial_unique_index_rejects_a_second_open_case_for_one_transaction(
    dsn: str,
) -> None:
    with (
        pytest.raises(psycopg.errors.UniqueViolation),
        psycopg.connect(dsn, autocommit=True) as conn,
        conn.cursor() as cur,
    ):
        cur.execute(
            "INSERT INTO cases (case_number, customer_id, transaction_id, session_id, "
            "idempotency_key, status, category, amount, currency, amount_provenance, "
            "domain_date, expected_first_response_date, created_at_utc, policy_version, "
            "reason_code, language) VALUES "
            "('CASE-A2-SECOND', 'CLI-A', 'TRX-A2', 'SESSION-1', 'IDEMP-A2-SECOND', "
            "'In Review', 'unrecognized_charge', 100.00, 'USD', 'reported', '2026-06-01', "
            "'2026-10-01', '2026-06-01 09:00:00+00', '2', 'eligible', 'en')"
        )
