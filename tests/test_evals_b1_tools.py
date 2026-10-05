"""
B1 Tool Schemas and Dispatch Tests
=====================================

Component: ``evals.runner.baselines.b1_tools``. ``get_policy`` needs only the real, file-backed
policy corpus. Every other tool needs a real, migrated Postgres; marked ``integration``, skipped
when ``DATABASE_URL`` is not set, matching this project's own convention.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest

from app.conversation.controller import HandoffOutbox
from app.domain.calendar import DateOrigin, DomainCalendar
from app.domain.policy.loader import load_policy
from app.domain.policy.models import DisputeCategory, Outcome, PolicyDecision, ReasonCode
from app.persistence.audit import PostgresAuditSink
from app.persistence.handoff_outbox import PostgresHandoffOutbox
from app.persistence.migrate import apply_migrations
from app.persistence.reads import PostgresToolPort
from app.retrieval.lexical import LexicalRetriever
from contracts.service_v1.cases import AmountProvenance, CaseRecord, CaseStatus, DisclosedAmount
from contracts.service_v1.cases import Money as CaseMoney
from contracts.service_v1.tools import EvaluateDisputeRequest, Tool, ToolFailure
from evals.runner.baselines.b1_tools import (
    _REQUEST_SUMMARY_OF,
    TOOL_SCHEMAS,
    B1ToolDispatcher,
    _to_json,
)
from evals.runner.baselines.naive_agent_client import ToolCall

_NOW = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)
_TODAY = date(2026, 6, 18)


def test_the_duplicated_request_summary_table_agrees_with_the_controller_s_own() -> None:
    """b1_tools' own module docstring calls this duplication deliberate and checked by test, not
    a divergent reimplementation; this is that check — every key and value must match exactly."""
    import app.conversation.controller as controller_module  # noqa: PLC0415 - internal table

    assert controller_module._REQUEST_SUMMARY_OF == _REQUEST_SUMMARY_OF


def test_every_tool_schema_names_one_of_the_seven_tools() -> None:
    names = {schema["name"] for schema in TOOL_SCHEMAS}
    assert names == {
        "list_transactions",
        "get_transaction",
        "list_dispute_cases",
        "get_case",
        "evaluate_dispute",
        "create_dispute_case",
        "get_policy",
        "handoff",
    }


def test_create_dispute_case_s_schema_never_declares_a_free_text_content_field() -> None:
    """The model must not even be able to express writing request_summary or reason_codes — the
    same 'inexpressible, not merely forbidden' discipline applied elsewhere."""
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "create_dispute_case")
    properties = schema["input_schema"]["properties"]  # type: ignore[index]
    assert set(properties) == {"transaction_ref", "category", "confirmed"}


def test_handoff_s_schema_never_declares_a_free_text_content_field() -> None:
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "handoff")
    properties = schema["input_schema"]["properties"]  # type: ignore[index]
    assert set(properties) == {"trigger"}


@pytest.fixture(scope="module")
def retriever() -> LexicalRetriever:
    return LexicalRetriever.from_corpus()


def test_get_policy_returns_matching_corpus_sections_as_plain_text(
    retriever: LexicalRetriever,
) -> None:
    dispatcher = B1ToolDispatcher(
        tool_port=object(),  # type: ignore[arg-type]  # never touched by get_policy
        retriever=retriever,
        outbox=object(),  # type: ignore[arg-type]
        policy=load_policy(),
        calendar=DomainCalendar(reference_date=_TODAY, origin=DateOrigin.SETTING),
        clock=lambda: _NOW,
        customer_id="CLI-UNUSED",
        lang="es",
    )
    call = ToolCall(id="t1", name="get_policy", input={"query": "cuánto tiempo tengo"})

    result = dispatcher.dispatch(call, session_id="s", turn_id="turn-00000001", trace_id="s")

    assert "filing-windows" in result or "section_id" in result


def test_get_policy_returns_an_empty_list_below_the_relevance_floor(
    retriever: LexicalRetriever,
) -> None:
    dispatcher = B1ToolDispatcher(
        tool_port=object(),  # type: ignore[arg-type]
        retriever=retriever,
        outbox=object(),  # type: ignore[arg-type]
        policy=load_policy(),
        calendar=DomainCalendar(reference_date=_TODAY, origin=DateOrigin.SETTING),
        clock=lambda: _NOW,
        customer_id="CLI-UNUSED",
        lang="es",
    )
    call = ToolCall(id="t1", name="get_policy", input={"query": "xyzxyzxyz nonsense query zzqq"})

    result = dispatcher.dispatch(call, session_id="s", turn_id="turn-00000001", trace_id="s")

    assert result == "[]"


class _FailingToolPort:
    """Stands in for a real `ToolPort` whose store call itself failed."""

    def evaluate_dispute(self, request: EvaluateDisputeRequest) -> ToolFailure:
        return ToolFailure(tool=Tool.EVALUATE_DISPUTE, cause="error")


def test_evaluate_dispute_passes_a_tool_failure_through_without_tracking_a_decision(
    retriever: LexicalRetriever,
) -> None:
    """A failed store call must reach the model as-is, and must never be mistaken for a real
    decision `create_dispute_case` could later match against."""
    dispatcher = B1ToolDispatcher(
        tool_port=_FailingToolPort(),  # type: ignore[arg-type]
        retriever=retriever,
        outbox=object(),  # type: ignore[arg-type]
        policy=load_policy(),
        calendar=DomainCalendar(reference_date=_TODAY, origin=DateOrigin.SETTING),
        clock=lambda: _NOW,
        customer_id="CLI-UNUSED",
        lang="es",
    )
    call = ToolCall(
        id="t1",
        name="evaluate_dispute",
        input={"transaction_ref": "TRX-UNUSED", "category": "unrecognized_charge"},
    )

    result = dispatcher.dispatch(call, session_id="s", turn_id="turn-00000001", trace_id="s")

    assert '"cause":"error"' in result.replace(" ", "")
    assert dispatcher._decisions == {}


class _UnmatchedToolPort:
    """Stands in for a `ToolPort` that finds no transaction the session's customer owns."""

    def evaluate_dispute(self, request: EvaluateDisputeRequest) -> None:
        return None


class _CaseListToolPort:
    """Stands in for a `ToolPort` whose case list is a fixed tuple."""

    def __init__(self, cases: tuple[CaseRecord, ...]) -> None:
        self._cases = cases

    def list_dispute_cases(self) -> tuple[CaseRecord, ...]:
        return self._cases


def _eligible_decision() -> PolicyDecision:
    return PolicyDecision(
        outcome=Outcome.ELIGIBLE,
        reason_code=ReasonCode.ELIGIBLE,
        policy_version="2",
        requires_confirmation=True,
        facts=(),
        transaction_ref="TRX-EARLIER",
        category=DisputeCategory.UNRECOGNIZED_CHARGE,
    )


def _dispatcher_over(tool_port: object, retriever: LexicalRetriever) -> B1ToolDispatcher:
    return B1ToolDispatcher(
        tool_port=tool_port,  # type: ignore[arg-type]
        retriever=retriever,
        outbox=object(),  # type: ignore[arg-type]
        policy=load_policy(),
        calendar=DomainCalendar(reference_date=_TODAY, origin=DateOrigin.SETTING),
        clock=lambda: _NOW,
        customer_id="CLI-UNUSED",
        lang="es",
    )


def test_evaluate_dispute_passes_an_unmatched_reference_through_without_tracking_a_decision(
    retriever: LexicalRetriever,
) -> None:
    """A reference the customer does not own comes back as no result; the model must read that
    as-is and the harness must not treat it as a decision."""
    dispatcher = _dispatcher_over(_UnmatchedToolPort(), retriever)
    earlier = _eligible_decision()
    dispatcher._decisions[("TRX-EARLIER", DisputeCategory.UNRECOGNIZED_CHARGE)] = earlier
    dispatcher.last_confirmable_decision = earlier
    call = ToolCall(
        id="t1",
        name="evaluate_dispute",
        input={"transaction_ref": "TRX-UNKNOWN", "category": "unrecognized_charge"},
    )

    result = dispatcher.dispatch(call, session_id="s", turn_id="turn-00000001", trace_id="s")

    assert result == "null"
    assert dispatcher._decisions == {("TRX-EARLIER", DisputeCategory.UNRECOGNIZED_CHARGE): earlier}
    assert dispatcher.last_confirmable_decision is earlier


def test_list_dispute_cases_dispatches_a_tuple_of_cases_as_a_json_array(
    retriever: LexicalRetriever,
) -> None:
    dispatcher = _dispatcher_over(_CaseListToolPort((_case_record("CASE-1"),)), retriever)
    call = ToolCall(id="t1", name="list_dispute_cases", input={})

    result = dispatcher.dispatch(call, session_id="s", turn_id="turn-00000001", trace_id="s")

    assert [item["case_number"] for item in json.loads(result)] == ["CASE-1"]


def test_list_dispute_cases_dispatches_an_empty_case_list_as_an_empty_array(
    retriever: LexicalRetriever,
) -> None:
    dispatcher = _dispatcher_over(_CaseListToolPort(()), retriever)
    call = ToolCall(id="t1", name="list_dispute_cases", input={})

    result = dispatcher.dispatch(call, session_id="s", turn_id="turn-00000001", trace_id="s")

    assert result == "[]"


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_transaction", {}),
        ("get_case", {}),
        ("get_policy", {}),
        ("handoff", {}),
        ("evaluate_dispute", {"category": "unrecognized_charge"}),
        ("evaluate_dispute", {"transaction_ref": "TRX-1"}),
        ("create_dispute_case", {"category": "unrecognized_charge"}),
        ("create_dispute_case", {"transaction_ref": "TRX-1"}),
    ],
)
def test_a_missing_required_argument_is_a_value_error_the_harness_records_per_case(
    retriever: LexicalRetriever, tool: str, arguments: dict[str, object]
) -> None:
    """The model is not bound by the tool schema; a call that omits a required argument must raise
    ``ValueError`` (a recorded case failure), never ``KeyError`` (which would stop the run)."""
    dispatcher = _dispatcher_over(object(), retriever)
    call = ToolCall(id="t1", name=tool, input=arguments)

    with pytest.raises(ValueError, match="required argument"):
        dispatcher.dispatch(call, session_id="s", turn_id="turn-00000001", trace_id="s")


def test_to_json_uses_model_dump_json_for_a_pydantic_value() -> None:
    value = ToolFailure(tool=Tool.EVALUATE_DISPUTE, cause="error")
    assert _to_json(value) == value.model_dump_json()


def test_to_json_falls_back_to_plain_json_dumps_for_a_non_pydantic_value() -> None:
    assert _to_json({"a": 1}) == '{"a": 1}'
    assert _to_json(None) == "null"


def _case_record(case_number: str) -> CaseRecord:
    return CaseRecord(
        case_number=case_number,
        status=CaseStatus.OPEN,
        transaction_ref="TX-1",
        category=DisputeCategory.UNRECOGNIZED_CHARGE,
        amount=DisclosedAmount(
            money=CaseMoney(amount=Decimal("100.00"), currency="USD"),
            provenance=AmountProvenance.REPORTED,
        ),
        domain_date=_TODAY,
        expected_first_response_date=date(2026, 7, 18),
        created_at_utc=_NOW,
        policy_version="2",
        reason_code=ReasonCode.ELIGIBLE,
        language="es",
    )


def test_to_json_renders_a_tuple_of_pydantic_values_as_a_json_array() -> None:
    """``list_dispute_cases`` returns a tuple of case records; the model must be able to read it."""
    records = (_case_record("D-1"), _case_record("D-2"))

    rendered = json.loads(_to_json(records))

    assert [row["case_number"] for row in rendered] == ["D-1", "D-2"]


def test_to_json_renders_an_empty_tuple_as_an_empty_array() -> None:
    assert _to_json(()) == "[]"


def test_dispatch_raises_for_an_unknown_tool(retriever: LexicalRetriever) -> None:
    dispatcher = B1ToolDispatcher(
        tool_port=object(),  # type: ignore[arg-type]
        retriever=retriever,
        outbox=object(),  # type: ignore[arg-type]
        policy=load_policy(),
        calendar=DomainCalendar(reference_date=_TODAY, origin=DateOrigin.SETTING),
        clock=lambda: _NOW,
        customer_id="CLI-UNUSED",
        lang="es",
    )
    call = ToolCall(id="t1", name="not_a_real_tool", input={})

    with pytest.raises(ValueError, match="unknown tool"):
        dispatcher.dispatch(call, session_id="s", turn_id="turn-00000001", trace_id="s")


# -----------------------------------------------------------------------------
# Everything else — needs the real store
# -----------------------------------------------------------------------------


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(value)
    with psycopg.connect(value) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute(
            "TRUNCATE TABLE cases, transactions, products, customers, audit_log, "
            "handoff_outbox CASCADE"
        )
        conn.commit()
    with psycopg.connect(value, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO customers (customer_id, first_name, last_name, country, "
            "customer_status) VALUES ('CLI-B1-A', 'First', 'Last', 'México', 'Active')"
        )
        cur.execute(
            "INSERT INTO products (product_id, customer_id, product_type, last4, "
            "product_status) VALUES ('PRD-B1-A', 'CLI-B1-A', 'Cuenta Corriente', '1234', 'Active')"
        )
        cur.execute(
            "INSERT INTO transactions (transaction_id, customer_id, product_id, "
            "transaction_date, transaction_type, merchant_name, amount, currency, amount_usd, "
            "amount_usd_provenance, transaction_status) VALUES "
            "('TRX-B1-A1', 'CLI-B1-A', 'PRD-B1-A', '2026-06-08 09:00:00', 'Purchase', "
            "'A Merchant', 100.00, 'USD', 100.00, 'reported', 'Approved'), "
            # Above the policy's escalate_amount_usd threshold (5000.00): evaluate_dispute
            # resolves this one to ESCALATE_AMOUNT_ABOVE_THRESHOLD, not ELIGIBLE, so the
            # turn-scoping tests below have a real escalate_* decision to isolate.
            "('TRX-B1-A2', 'CLI-B1-A', 'PRD-B1-A', '2026-06-09 09:00:00', 'Purchase', "
            "'A Merchant', 6000.00, 'USD', 6000.00, 'reported', 'Approved')"
        )
    return value


def _dispatcher(dsn: str, retriever: LexicalRetriever) -> B1ToolDispatcher:
    policy = load_policy()
    tool_port = PostgresToolPort(
        dsn,
        PostgresAuditSink(dsn),
        policy,
        customer_id="CLI-B1-A",
        session_id="SESSION-B1-1",
        trace_id="SESSION-B1-1",
        domain_date=_TODAY,
        now=lambda: _NOW,
        language="es",
        case_create_session_cap=3,
    )
    outbox: HandoffOutbox = PostgresHandoffOutbox(dsn)
    return B1ToolDispatcher(
        tool_port=tool_port,
        retriever=retriever,
        outbox=outbox,
        policy=policy,
        calendar=DomainCalendar(reference_date=_TODAY, origin=DateOrigin.SETTING),
        clock=lambda: _NOW,
        customer_id="CLI-B1-A",
        lang="es",
    )


@pytest.mark.integration
def test_list_transactions_returns_the_customer_s_own_transaction(
    dsn: str, retriever: LexicalRetriever
) -> None:
    dispatcher = _dispatcher(dsn, retriever)
    call = ToolCall(id="t1", name="list_transactions", input={})

    result = dispatcher.dispatch(call, session_id="s", turn_id="turn-00000001", trace_id="s")

    assert "TRX-B1-A1" in result


@pytest.mark.integration
def test_get_transaction_returns_the_named_transaction(
    dsn: str, retriever: LexicalRetriever
) -> None:
    dispatcher = _dispatcher(dsn, retriever)
    call = ToolCall(id="t1", name="get_transaction", input={"ref": "TRX-B1-A1"})

    result = dispatcher.dispatch(call, session_id="s", turn_id="turn-00000001", trace_id="s")

    assert "TRX-B1-A1" in result


@pytest.mark.integration
def test_evaluate_dispute_returns_an_eligible_decision(
    dsn: str, retriever: LexicalRetriever
) -> None:
    dispatcher = _dispatcher(dsn, retriever)
    call = ToolCall(
        id="t1",
        name="evaluate_dispute",
        input={"transaction_ref": "TRX-B1-A1", "category": "unrecognized_charge"},
    )

    result = dispatcher.dispatch(call, session_id="s", turn_id="turn-00000001", trace_id="s")

    assert "eligible" in result
    assert dispatcher._decisions[("TRX-B1-A1", DisputeCategory.UNRECOGNIZED_CHARGE)] is not None


@pytest.mark.integration
def test_create_dispute_case_files_a_case_only_after_a_tracked_eligible_decision(
    dsn: str, retriever: LexicalRetriever
) -> None:
    """The model's create_dispute_case call carries no decision of its own; the dispatcher
    supplies the one it already tracked from evaluate_dispute, never trusting the model for it."""
    dispatcher = _dispatcher(dsn, retriever)
    dispatcher.dispatch(
        ToolCall(
            id="t1",
            name="evaluate_dispute",
            input={"transaction_ref": "TRX-B1-A1", "category": "unrecognized_charge"},
        ),
        session_id="s",
        turn_id="turn-00000001",
        trace_id="s",
    )

    result = dispatcher.dispatch(
        ToolCall(
            id="t2",
            name="create_dispute_case",
            input={
                "transaction_ref": "TRX-B1-A1",
                "category": "unrecognized_charge",
                "confirmed": True,
            },
        ),
        session_id="s",
        turn_id="turn-00000002",
        trace_id="s",
    )

    assert '"created":true' in result.replace(" ", "")


@pytest.mark.integration
def test_create_dispute_case_is_refused_with_no_tracked_decision(
    dsn: str, retriever: LexicalRetriever
) -> None:
    """A filing attempt for a transaction/category the dispatcher never evaluated is refused,
    never silently trusted from the model's own tool-call arguments."""
    dispatcher = _dispatcher(dsn, retriever)

    result = dispatcher.dispatch(
        ToolCall(
            id="t1",
            name="create_dispute_case",
            input={
                "transaction_ref": "TRX-B1-A1",
                "category": "unrecognized_charge",
                "confirmed": True,
            },
        ),
        session_id="s",
        turn_id="turn-00000001",
        trace_id="s",
    )

    assert '"created":false' in result.replace(" ", "")


@pytest.mark.integration
def test_handoff_writes_a_real_outbox_row_and_records_the_ticket(
    dsn: str, retriever: LexicalRetriever
) -> None:
    dispatcher = _dispatcher(dsn, retriever)
    dispatcher.start_turn()
    call = ToolCall(id="t1", name="handoff", input={"trigger": "fraud_report"})

    result = dispatcher.dispatch(call, session_id="s", turn_id="turn-00000001", trace_id="s")

    assert dispatcher.handoff_ticket is not None
    assert dispatcher.handoff_ticket in result
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT request_summary FROM handoff_outbox WHERE ticket_ref = %s",
            (dispatcher.handoff_ticket,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "Customer reported a possible fraud."


def _escalate_amount_decision(dispatcher: B1ToolDispatcher, *, turn_id: str) -> None:
    """Produce a real escalate_amount_above_threshold decision (TRX-B1-A2 is above the policy's
    escalate_amount_usd threshold) — the only decision shape that can actually reach
    ``_handoff``'s ``escalate_``-prefix filter, unlike an ELIGIBLE one."""
    dispatcher.dispatch(
        ToolCall(
            id="t1",
            name="evaluate_dispute",
            input={"transaction_ref": "TRX-B1-A2", "category": "unrecognized_charge"},
        ),
        session_id="s",
        turn_id=turn_id,
        trace_id="s",
    )


@pytest.mark.integration
def test_handoff_attaches_this_turn_s_escalate_reason_code(
    dsn: str, retriever: LexicalRetriever
) -> None:
    dispatcher = _dispatcher(dsn, retriever)

    dispatcher.start_turn()
    _escalate_amount_decision(dispatcher, turn_id="turn-00000001")
    dispatcher.dispatch(
        ToolCall(id="t2", name="handoff", input={"trigger": "amount_review"}),
        session_id="s",
        turn_id="turn-00000001",
        trace_id="s",
    )

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT reason_code FROM handoff_reason_codes WHERE ticket_ref = %s",
            (dispatcher.handoff_ticket,),
        )
        rows = cur.fetchall()
    assert rows == [("escalate_amount_above_threshold",)]


@pytest.mark.integration
def test_handoff_does_not_attach_an_older_turn_s_escalate_reason_code(
    dsn: str, retriever: LexicalRetriever
) -> None:
    dispatcher = _dispatcher(dsn, retriever)

    dispatcher.start_turn()
    _escalate_amount_decision(dispatcher, turn_id="turn-00000001")

    # A new turn starts: the earlier turn's escalate_* decision must not leak into this turn's
    # handoff, even though it is still tracked in _decisions for create_dispute_case's own lookup.
    dispatcher.start_turn()
    dispatcher.dispatch(
        ToolCall(id="t2", name="handoff", input={"trigger": "customer_request"}),
        session_id="s",
        turn_id="turn-00000002",
        trace_id="s",
    )

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT reason_code FROM handoff_reason_codes WHERE ticket_ref = %s",
            (dispatcher.handoff_ticket,),
        )
        rows = cur.fetchall()
    assert rows == []
