"""
Scoped Reads Tests
==================

Component: ``app.persistence.reads``. Needs a real, migrated Postgres — the isolation guarantees
under test are about what SQL actually returns, not what a fake could be made to return; marked
``integration``, skipped when ``DATABASE_URL`` is not set.

Two seeded customers, A and B (AC-E4-06), each with their own product, transaction and case; the
matrix below calls every reference tool with a reference of customer B's, as given, in a
different letter case and with surrounding spaces.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

import psycopg
import pytest

from app.domain.policy.loader import load_policy
from app.persistence.audit import PostgresAuditSink
from app.persistence.migrate import apply_migrations
from app.persistence.reads import PostgresToolPort
from contracts.service_v1.audit import AuditRecord, AuditSink
from contracts.service_v1.cases import Lang
from contracts.service_v1.tools import (
    EvaluateDisputeRequest,
    ToolFailure,
    TransactionFilters,
)

DOMAIN_DATE = date(2026, 6, 18)
NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


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
        domain_date=DOMAIN_DATE,
        now=lambda: NOW,
        language=language,
        case_create_session_cap=case_create_session_cap,
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
        cur.executemany(
            "INSERT INTO customers (customer_id, first_name, last_name, country, "
            "customer_status) VALUES (%s, 'First', 'Last', 'México', 'Active')",
            [("CLI-A",), ("CLI-B",)],
        )
        cur.executemany(
            "INSERT INTO products (product_id, customer_id, product_type, last4, "
            "product_status) VALUES (%s, %s, 'Cuenta Corriente', '1234', 'Active')",
            [("PRD-A", "CLI-A"), ("PRD-B", "CLI-B")],
        )
        cur.executemany(
            "INSERT INTO transactions (transaction_id, customer_id, product_id, "
            "transaction_date, transaction_type, merchant_name, amount, currency, amount_usd, "
            "amount_usd_provenance, transaction_status) VALUES "
            "(%s, %s, %s, %s, 'Purchase', 'A Merchant', 100.00, 'USD', "
            "100.00, 'reported', 'Approved')",
            [
                # Has an open case (CASE-A1); the earlier of A's two, so list order is exact.
                ("TRX-A1", "CLI-A", "PRD-A", "2026-06-08 09:00:00"),
                # No case: the eligible-evaluation fixture; the later of A's two.
                ("TRX-A2", "CLI-A", "PRD-A", "2026-06-09 09:00:00"),
                ("TRX-B1", "CLI-B", "PRD-B", "2026-06-08 09:00:00"),
            ],
        )
        cur.executemany(
            "INSERT INTO cases (case_number, customer_id, transaction_id, session_id, "
            "idempotency_key, status, category, amount, currency, amount_provenance, "
            "domain_date, expected_first_response_date, created_at_utc, policy_version, "
            "reason_code, language) VALUES "
            "(%s, %s, %s, 'SESSION-0', %s, 'Open', 'unrecognized_charge', 100.00, 'USD', "
            "'reported', '2026-06-01', '2026-10-01', '2026-06-01 09:00:00+00', '2', "
            "'eligible', 'en')",
            [
                ("CASE-A1", "CLI-A", "TRX-A1", "IDEMP-A"),
                ("CASE-B1", "CLI-B", "TRX-B1", "IDEMP-B"),
            ],
        )
    return value


# -----------------------------------------------------------------------------
# Scoping and page shape
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_list_transactions_returns_only_the_session_customers_own_rows(dsn: str) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    page = port.list_transactions(TransactionFilters())

    assert not isinstance(page, ToolFailure)
    # Most recent first: A2 (06-09) before A1 (06-08); never B1, another customer's row.
    assert [item.ref for item in page.items] == ["TRX-A2", "TRX-A1"]
    assert page.total_count == 2
    assert sink.records[-1].action.value == "transactions_listed"


@pytest.mark.integration
def test_list_dispute_cases_returns_only_the_session_customers_own_rows(dsn: str) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    cases = port.list_dispute_cases()

    assert not isinstance(cases, ToolFailure)
    assert [case.case_number for case in cases] == ["CASE-A1"]
    assert sink.records[-1].action.value == "cases_listed"


# -----------------------------------------------------------------------------
# AC-E4-06: a foreign reference answers exactly like a missing one
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    # The exact-case reference is a real row belonging to customer B, so it exercises the
    # foreign-ownership branch (audited "probed"); the lookup itself is exact-match, so a
    # different case or surrounding spaces never matches any row and exercises the plain
    # not-found branch instead (audited "viewed") — both branches answer None either way
    # (AC-E4-06), which is what every variant here asserts; the audited action, asserted per
    # variant, is what tells the two branches apart.
    ("ref", "audited_as"),
    [
        ("TRX-B1", "transaction_probed"),
        ("trx-b1", "transaction_viewed"),
        ("Trx-B1", "transaction_viewed"),
        ("  TRX-B1  ", "transaction_viewed"),
    ],
    ids=["as-given", "lower", "mixed", "spaces"],
)
@pytest.mark.integration
def test_get_transaction_answers_the_same_for_a_foreign_and_a_nonexistent_reference(
    dsn: str, ref: str, audited_as: str
) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    foreign = port.get_transaction(ref)
    missing = port.get_transaction("TRX-NOBODY")

    assert foreign is None
    assert missing is None
    assert sink.records[0].action.value == audited_as
    assert sink.records[1].action.value == "transaction_viewed"


@pytest.mark.integration
def test_get_transaction_returns_the_session_customers_own_row(dsn: str) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    fact = port.get_transaction("TRX-A1")

    assert not isinstance(fact, ToolFailure)
    assert fact is not None
    assert fact.ref == "TRX-A1"


@pytest.mark.integration
def test_get_transaction_audits_a_foreign_reference_distinguishably_from_a_genuine_miss(
    dsn: str,
) -> None:
    """Issue #73: the customer-visible result is identical either way; the trail is not."""
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    port.get_transaction("TRX-B1")
    port.get_transaction("TRX-NOBODY")

    actions = [record.action.value for record in sink.records]
    assert actions == ["transaction_probed", "transaction_viewed"]


@pytest.mark.integration
def test_get_case_answers_the_same_for_a_foreign_and_a_nonexistent_reference(dsn: str) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    assert port.get_case("CASE-B1") is None
    assert port.get_case("CASE-NOBODY") is None


@pytest.mark.integration
def test_get_case_audits_a_foreign_reference_distinguishably_from_a_genuine_miss(
    dsn: str,
) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    port.get_case("CASE-B1")
    port.get_case("CASE-NOBODY")

    actions = [record.action.value for record in sink.records]
    assert actions == ["case_probed", "case_viewed"]


@pytest.mark.integration
def test_get_case_returns_the_session_customers_own_row(dsn: str) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    record = port.get_case("CASE-A1")

    assert not isinstance(record, ToolFailure)
    assert record is not None
    assert record.case_number == "CASE-A1"


@pytest.mark.integration
def test_evaluate_dispute_answers_the_same_failure_for_a_foreign_and_a_missing_reference(
    dsn: str,
) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    foreign = port.evaluate_dispute(
        EvaluateDisputeRequest(transaction_ref="TRX-B1", category="unrecognized_charge")
    )
    missing = port.evaluate_dispute(
        EvaluateDisputeRequest(transaction_ref="TRX-NOBODY", category="unrecognized_charge")
    )

    assert isinstance(foreign, ToolFailure) and isinstance(missing, ToolFailure)
    assert (foreign.cause, foreign.retryable) == (missing.cause, missing.retryable)
    assert foreign.retryable is False


@pytest.mark.integration
def test_evaluate_dispute_decides_on_the_session_customers_own_transaction(dsn: str) -> None:
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    decision = port.evaluate_dispute(
        EvaluateDisputeRequest(transaction_ref="TRX-A2", category="unrecognized_charge")
    )

    assert not isinstance(decision, ToolFailure)
    assert decision.outcome.value == "eligible"
    assert sink.records[-1].action.value == "dispute_evaluated"
    assert sink.records[-1].reason_code is not None
    assert sink.records[-1].policy_version == decision.policy_version


@pytest.mark.integration
def test_evaluate_dispute_reads_an_open_case_for_the_transaction_from_the_store(dsn: str) -> None:
    """TRX-A1 already has an open case (CASE-A1): the gate is a real query, not a stub."""
    sink = _RecordingSink(dsn)
    port = _port(dsn, sink, customer_id="CLI-A")

    decision = port.evaluate_dispute(
        EvaluateDisputeRequest(transaction_ref="TRX-A1", category="unrecognized_charge")
    )

    assert not isinstance(decision, ToolFailure)
    assert decision.outcome.value == "ineligible"
    assert decision.reason_code.value == "duplicate_open_case"


# -----------------------------------------------------------------------------
# Fail closed
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_a_read_that_cannot_be_audited_raises_rather_than_being_returned(dsn: str) -> None:
    port = _port(dsn, _FailingSink(), customer_id="CLI-A")

    with pytest.raises(RuntimeError, match="audit store is down"):
        port.get_transaction("TRX-A1")


@pytest.mark.integration
def test_a_store_failure_is_a_retryable_tool_failure_and_is_not_audited(dsn: str) -> None:
    sink = _RecordingSink(dsn)
    port = _port("postgresql://unreachable.invalid/nowhere", sink, customer_id="CLI-A")

    result = port.list_transactions(TransactionFilters())

    assert isinstance(result, ToolFailure)
    assert result.retryable is True
    assert sink.records == []
