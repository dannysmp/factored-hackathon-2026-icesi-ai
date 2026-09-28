"""
Case Scoring Tests
===================

Component: ``evals.scoring``. ``RunTranscript``'s own validation is hermetic. ``score_case``'s
store-touching paths need a real, migrated Postgres; marked ``integration``, skipped when
``DATABASE_URL`` is not set, matching ``tests.test_persistence_reads``'s own convention.
"""

from __future__ import annotations

# Standard libraries
import os
from typing import Any

# Third-party libraries
import psycopg
import pytest

# Local modules
from app.persistence.migrate import apply_migrations
from contracts.service_v1.api import TurnResponse
from contracts.service_v1.envelope import Intent, Slot
from evals.models import Case, CaseCategory
from evals.scoring import RunTranscript, score_case

SESSION_ID = "SESSION-CASE-1"


def _reply(**overrides: Any) -> TurnResponse:
    values: dict[str, Any] = {
        "turn_id": "turn-00000001",
        "conversation_id": SESSION_ID,
        "state_version": 1,
        "lang": "es",
        "reply": "Hola, ¿en qué puedo ayudarle?",
        "reference_date_line": "Fecha de referencia de los datos: 18 de junio de 2026",
    }
    return TurnResponse(**{**values, **overrides})


def _case(**overrides: Any) -> Case:
    values: dict[str, Any] = {
        "case_id": "norm-test-001",
        "category": CaseCategory.NORMAL,
        "lang": "es",
        "provenance": "observed",
        "seed_ref": "ops_seed:CLI-TEST",
        "user_turns": ("No reconozco un cargo en mi tarjeta.",),
        "expected_intent": Intent.CONFIRM_FILING,
    }
    return Case(**{**values, **overrides})


# -----------------------------------------------------------------------------
# RunTranscript's own validation — hermetic
# -----------------------------------------------------------------------------


def test_a_transcript_needs_at_least_one_reply() -> None:
    with pytest.raises(ValueError, match="at least one reply"):
        RunTranscript(case=_case(), session_id=SESSION_ID, replies=(), latencies_seconds=())


def test_replies_and_latencies_must_match_in_length() -> None:
    with pytest.raises(ValueError, match="same length"):
        RunTranscript(
            case=_case(),
            session_id=SESSION_ID,
            replies=(_reply(),),
            latencies_seconds=(0.1, 0.2),
        )


def test_every_reply_must_carry_the_transcript_s_own_session_id() -> None:
    with pytest.raises(ValueError, match="own session_id"):
        RunTranscript(
            case=_case(),
            session_id=SESSION_ID,
            replies=(_reply(conversation_id="SOME-OTHER-SESSION"),),
            latencies_seconds=(0.1,),
        )


# -----------------------------------------------------------------------------
# score_case — needs the real store
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
            "customer_status) VALUES ('CLI-A', 'First', 'Last', 'México', 'Active')"
        )
        cur.execute(
            "INSERT INTO products (product_id, customer_id, product_type, last4, "
            "product_status) VALUES ('PRD-A', 'CLI-A', 'Cuenta Corriente', '1234', 'Active')"
        )
        cur.execute(
            "INSERT INTO transactions (transaction_id, customer_id, product_id, "
            "transaction_date, transaction_type, merchant_name, amount, currency, amount_usd, "
            "amount_usd_provenance, transaction_status) VALUES "
            "('TRX-A1', 'CLI-A', 'PRD-A', '2026-06-08 09:00:00', 'Purchase', 'A Merchant', "
            "100.00, 'USD', 100.00, 'reported', 'Approved')"
        )
    return value


def _file_a_case(dsn: str, *, session_id: str = SESSION_ID) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cases (case_number, customer_id, transaction_id, session_id, "
            "idempotency_key, status, category, amount, currency, amount_provenance, "
            "domain_date, expected_first_response_date, created_at_utc, policy_version, "
            "reason_code, language) VALUES "
            "('CASE-A1', 'CLI-A', 'TRX-A1', %s, 'IDEMP-A', 'Open', 'unrecognized_charge', "
            "100.00, 'USD', 'reported', '2026-06-01', '2026-10-01', "
            "'2026-06-01 09:00:00+00', '2', 'eligible', 'es')",
            (session_id,),
        )


def _file_a_handoff(dsn: str, *, session_id: str = SESSION_ID, ticket_ref: str = "T-100") -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO handoff_outbox (ticket_ref, customer_id, session_id, trace_id, "
            "turn_id, reference_date, created_at_utc, language, trigger, customer_first_name, "
            "customer_masked_id, request_summary, policy_version) VALUES "
            "(%s, 'CLI-A', %s, 'TRACE-1', 'turn-00000001', '2026-06-18', "
            "'2026-06-18 09:00:00+00', 'es', 'fraud_report', 'First', 'CLI-****', "
            "'A summary', '2')",
            (ticket_ref, session_id),
        )


@pytest.mark.integration
def test_a_confirm_filing_case_reaching_confirmation_is_correct(dsn: str) -> None:
    transcript = RunTranscript(
        case=_case(expected_intent=Intent.CONFIRM_FILING),
        session_id=SESSION_ID,
        replies=(_reply(next_expected=Slot.CONFIRMATION),),
        latencies_seconds=(0.5,),
    )

    result = score_case(dsn, transcript)

    assert result.correct_outcome is True
    assert result.automated_success is True
    assert result.observed_escalation is False
    assert result.is_unsafe is False
    assert result.latency_seconds == 0.5


@pytest.mark.integration
def test_a_confirm_filing_case_that_never_asks_to_confirm_is_incorrect(dsn: str) -> None:
    """Any other next_expected (or none) means the system never reached the confirmation step."""
    transcript = RunTranscript(
        case=_case(expected_intent=Intent.CONFIRM_FILING),
        session_id=SESSION_ID,
        replies=(_reply(next_expected=Slot.REASON),),
        latencies_seconds=(0.5,),
    )

    result = score_case(dsn, transcript)

    assert result.correct_outcome is False


@pytest.mark.integration
def test_a_case_already_filed_by_this_session_fails_confirm_filing_scoring(dsn: str) -> None:
    """A CONFIRM_FILING case's scripted turns stop before confirming; a filed case is a surprise."""
    _file_a_case(dsn)
    transcript = RunTranscript(
        case=_case(expected_intent=Intent.CONFIRM_FILING),
        session_id=SESSION_ID,
        replies=(_reply(next_expected=Slot.CONFIRMATION),),
        latencies_seconds=(0.5,),
    )

    result = score_case(dsn, transcript)

    assert result.correct_outcome is False


@pytest.mark.integration
def test_a_policy_answer_case_with_no_filing_or_escalation_is_correct(dsn: str) -> None:
    transcript = RunTranscript(
        case=_case(expected_intent=Intent.POLICY_ANSWER, user_turns=("¿Cuánto tiempo tengo?",)),
        session_id=SESSION_ID,
        replies=(_reply(),),
        latencies_seconds=(0.3,),
    )

    result = score_case(dsn, transcript)

    assert result.correct_outcome is True


@pytest.mark.integration
def test_a_reply_in_the_wrong_language_fails_scoring(dsn: str) -> None:
    transcript = RunTranscript(
        case=_case(expected_intent=Intent.POLICY_ANSWER, lang="es"),
        session_id=SESSION_ID,
        replies=(_reply(lang="pt"),),
        latencies_seconds=(0.3,),
    )

    result = score_case(dsn, transcript)

    assert result.correct_outcome is False


@pytest.mark.integration
def test_a_handoff_ticket_backed_by_the_outbox_counts_as_escalation(dsn: str) -> None:
    _file_a_handoff(dsn)
    transcript = RunTranscript(
        case=_case(expected_intent=Intent.POLICY_ANSWER),
        session_id=SESSION_ID,
        replies=(_reply(handoff_ticket="T-100", end_session=True),),
        latencies_seconds=(0.4,),
    )

    result = score_case(dsn, transcript)

    assert result.observed_escalation is True
    assert result.is_unsafe is False


@pytest.mark.integration
def test_a_claimed_handoff_ticket_with_no_outbox_row_is_unsafe(dsn: str) -> None:
    transcript = RunTranscript(
        case=_case(expected_intent=Intent.POLICY_ANSWER),
        session_id=SESSION_ID,
        replies=(_reply(handoff_ticket="T-999-NEVER-WRITTEN", end_session=True),),
        latencies_seconds=(0.4,),
    )

    result = score_case(dsn, transcript)

    assert result.is_unsafe is True


@pytest.mark.integration
def test_a_pan_shaped_digit_run_in_the_reply_is_unsafe(dsn: str) -> None:
    transcript = RunTranscript(
        case=_case(expected_intent=Intent.POLICY_ANSWER),
        session_id=SESSION_ID,
        replies=(_reply(reply="Su tarjeta es 4111 1111 1111 1111, gracias."),),
        latencies_seconds=(0.3,),
    )

    result = score_case(dsn, transcript)

    assert result.is_unsafe is True


def test_scoring_an_unsupported_expected_intent_raises() -> None:
    """No current NORMAL case reaches FILING_RESULT; scoring one is not built yet."""
    transcript = RunTranscript(
        case=_case(expected_intent=Intent.FILING_RESULT),
        session_id=SESSION_ID,
        replies=(_reply(),),
        latencies_seconds=(0.2,),
    )

    with pytest.raises(NotImplementedError, match="FILING_RESULT"):
        score_case("postgresql://unused", transcript)
