"""
Case Creation Tests
====================

Component: ``app.persistence.reads.PostgresToolPort.create_dispute_case``. Needs a real, migrated
Postgres — the constraints under test (migration 0004's reason-code check and the partial unique
index) and the race-losing paths only exist at the store; marked ``integration``, skipped when
``DATABASE_URL`` is not set. Mirrors ``tests/test_persistence_reads.py``'s fixture style: a
self-contained seed, not shared through a ``conftest.py``.

The create tool never evaluates policy (ADR-3): every test hands it an already-decided
``PolicyDecision`` built by ``_decision`` below, exactly as the controller would after evaluating
one itself, and checks only the permission invariants the tool enforces on top of it.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

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
    """An ``AuditSink`` that always refuses to write, for the fail-closed test (AC-E4-19)."""

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
    """``decision`` defaults to a matching, eligible one; pass ``None`` explicitly for AC-E4-13's
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
# Happy path (AC-E4-17)
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
    assert record.expected_first_response_date >= record.domain_date
    assert record.created_at_utc == NOW
    assert record.policy_version == POLICY_VERSION
    assert record.language == "en"


# -----------------------------------------------------------------------------
# AC-E4-12 / AC-E4-13: confirmation and decision
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
# AC-E4-14: the case filed is always the one the customer saw
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
# AC-E4-15: idempotency replay, sequential and racing
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
    # Exactly one case_created audit record: the replay is observable in the logs, not the trail.
    created_records = [r for r in sink.records if r.action.value == "case_created"]
    assert len(created_records) == 1


@pytest.mark.integration
def test_a_race_on_the_same_key_and_payload_still_yields_one_case(dsn: str) -> None:
    """Both calls resolve the pre-check to "no existing row"; the store's own unique constraint
    (``cases_customer_idempotency_key_unique``, migration 0001) is what actually decides the
    race, exercised here by inserting the competing row directly, after the pre-check, before
    the tool's own insert."""
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")
    request = _request(transaction_ref="TRX-A1", idempotency_key="IDEMP-RACE")

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cases (case_number, customer_id, transaction_id, session_id, "
            "idempotency_key, status, category, amount, currency, amount_provenance, "
            "domain_date, expected_first_response_date, created_at_utc, policy_version, "
            "reason_code, language) VALUES "
            "('CASE-RACE-WINNER', 'CLI-A', 'TRX-A1', 'SESSION-OTHER', 'IDEMP-RACE', 'Open', "
            "'unrecognized_charge', 100.00, 'USD', 'reported', '2026-06-01', '2026-10-01', "
            "'2026-06-01 09:00:00+00', %s, 'eligible', 'en')",
            (POLICY_VERSION,),
        )

    result = port.create_dispute_case(request)

    assert isinstance(result, CreateDisputeCaseResult)
    assert result.created is True
    assert result.case_number == "CASE-RACE-WINNER"


# -----------------------------------------------------------------------------
# AC-E4-16: a reused key with a different payload, and a duplicate open case
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_the_same_key_with_a_different_payload_is_refused_idempotency_conflict(dsn: str) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")
    port.create_dispute_case(_request(transaction_ref="TRX-A1", idempotency_key="IDEMP-REUSED"))

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
    assert len(_case_numbers(port)) == 2  # The one filed, plus the seeded CASE-A2-OPEN.


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
# AC-E4-19: fail closed on the audit write
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
