"""
Judge Grounding Facts Tests
=============================

Component: ``evals.facts``. ``assemble_facts_and_sources`` always queries the store (to rule out a
filed case for the session), so every test here needs a real, migrated Postgres; marked
``integration``, skipped when ``DATABASE_URL`` is not set, matching ``tests.test_evals_scoring``'s
own convention. The policy-section half itself reads the real, committed corpus, with no need for
a filed case to be present.
"""

from __future__ import annotations

# Standard libraries
import os
from typing import Any

# Third-party libraries
import psycopg
import pytest

# Local modules
from app.domain.policy.models import DisputeCategory
from app.llm.masking import PLACEHOLDER
from app.persistence.migrate import apply_migrations
from contracts.service_v1.api import TurnResponse
from contracts.service_v1.envelope import Intent
from evals.facts import assemble_facts_and_sources, attach_masked_transcript
from evals.metrics import CaseResult
from evals.models import Case, CaseCategory
from evals.scoring import RunTranscript

SESSION_ID = "SESSION-FACTS-1"

# A well-known Luhn-valid test PAN (Visa), the same one tests.test_masking already uses.
_VISA = "4111111111111111"


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
        "seed_ref": "ops_seed:TRX-TEST",
        "user_turns": ("No reconozco un cargo en mi tarjeta.",),
        "expected_intent": Intent.CONFIRM_FILING,
        "expected_category": DisputeCategory.UNRECOGNIZED_CHARGE,
    }
    return Case(**{**values, **overrides})


def _transcript(
    *, replies: tuple[TurnResponse, ...] | None = None, **case_overrides: Any
) -> RunTranscript:
    replies = replies if replies is not None else (_reply(),)
    return RunTranscript(
        case=_case(**case_overrides),
        session_id=SESSION_ID,
        replies=replies,
        latencies_seconds=tuple(0.1 for _ in replies),
    )


def _case_result(**overrides: Any) -> CaseResult:
    defaults: dict[str, Any] = {
        "case_id": "norm-test-001",
        "is_adversarial": False,
        "expected_escalation": False,
        "observed_escalation": False,
        "automation_attempted": True,
        "correct_outcome": True,
    }
    return CaseResult(**{**defaults, **overrides})


# -----------------------------------------------------------------------------
# Policy-section half — needs the real store only to confirm no case was filed
# -----------------------------------------------------------------------------


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(value)
    with psycopg.connect(value) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute("TRUNCATE TABLE cases, transactions, products, customers CASCADE")
        conn.commit()
    return value


@pytest.mark.integration
def test_a_policy_answer_case_is_grounded_in_its_declared_section(dsn: str) -> None:
    transcript = _transcript(
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="filing-windows",
        expected_category=None,
        user_turns=("¿Cuántos días tengo?",),
    )

    facts = assemble_facts_and_sources(dsn, transcript)

    assert "filing-windows" in facts
    assert "120" in facts  # the unrecognized-charge filing window, stated in the corpus body


@pytest.mark.integration
def test_a_case_with_no_section_and_no_filed_case_states_there_are_no_known_facts(
    dsn: str,
) -> None:
    facts = assemble_facts_and_sources(dsn, _transcript())

    assert "no case-specific facts" in facts.lower()


# -----------------------------------------------------------------------------
# Filed-transaction half — needs the real store
# -----------------------------------------------------------------------------


def _seed_transaction_and_case(dsn: str, *, session_id: str = SESSION_ID) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
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


@pytest.mark.integration
def test_a_filed_case_is_grounded_in_its_own_transaction(dsn: str) -> None:
    _seed_transaction_and_case(dsn)

    facts = assemble_facts_and_sources(dsn, _transcript())

    assert "A Merchant" in facts
    assert "100.00 USD" in facts
    assert "Approved" in facts


@pytest.mark.integration
def test_a_filed_case_for_a_different_session_is_not_included(dsn: str) -> None:
    _seed_transaction_and_case(dsn, session_id="SOME-OTHER-SESSION")

    facts = assemble_facts_and_sources(dsn, _transcript())

    assert "A Merchant" not in facts
    assert "no case-specific facts" in facts.lower()


@pytest.mark.integration
def test_a_policy_answer_case_that_also_filed_includes_both(dsn: str) -> None:
    _seed_transaction_and_case(dsn)

    facts = assemble_facts_and_sources(
        dsn,
        _transcript(
            expected_intent=Intent.POLICY_ANSWER,
            expected_policy_section_id="evidence",
            expected_category=None,
            user_turns=("¿Qué necesito tener listo?",),
        ),
    )

    assert "evidence" in facts
    assert "A Merchant" in facts


# -----------------------------------------------------------------------------
# attach_masked_transcript — the transcript capture capability
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_attach_masked_transcript_fills_both_fields(dsn: str) -> None:
    transcript = _transcript()

    result = attach_masked_transcript(dsn, transcript, _case_result())

    assert result.reply_text == "Hola, ¿en qué puedo ayudarle?"
    assert "no case-specific facts" in (result.facts_and_sources or "").lower()


@pytest.mark.integration
def test_attach_masked_transcript_joins_every_reply_in_order(dsn: str) -> None:
    transcript = _transcript(
        replies=(
            _reply(turn_id="turn-00000001", reply="Primero."),
            _reply(turn_id="turn-00000002", reply="Segundo."),
        )
    )

    result = attach_masked_transcript(dsn, transcript, _case_result())

    assert result.reply_text == "Primero.\n\nSegundo."


@pytest.mark.integration
def test_attach_masked_transcript_masks_a_pan_shaped_reply(dsn: str) -> None:
    transcript = _transcript(replies=(_reply(reply=f"Su número es {_VISA}."),))

    result = attach_masked_transcript(dsn, transcript, _case_result())

    assert _VISA not in (result.reply_text or "")
    assert PLACEHOLDER in (result.reply_text or "")


@pytest.mark.integration
def test_attach_masked_transcript_masks_a_pan_shaped_grounding_fact(dsn: str) -> None:
    """Revert-check for the "mask unconditionally, no trusted-origin carve-out" rule: a fact
    assembled from the store, not from the customer, is masked exactly the same way a reply is."""
    _seed_transaction_and_case(dsn)
    transcript = _transcript()

    result = attach_masked_transcript(dsn, transcript, _case_result())
    assert "A Merchant" in (result.facts_and_sources or "")  # sanity: real facts were assembled

    # Now prove the masking call is genuinely reached, not merely a no-op that happens to pass:
    # a transaction whose merchant name is itself PAN-shaped must not survive into the attached
    # field unmasked.
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE transactions SET merchant_name = %s WHERE transaction_id = 'TRX-A1'",
            (_VISA,),
        )

    result = attach_masked_transcript(dsn, transcript, _case_result())

    assert _VISA not in (result.facts_and_sources or "")
    assert PLACEHOLDER in (result.facts_and_sources or "")


@pytest.mark.integration
def test_attach_masked_transcript_preserves_every_other_field(dsn: str) -> None:
    original = _case_result(case_id="norm-test-001", correct_outcome=False, is_unsafe=True)

    result = attach_masked_transcript(dsn, _transcript(), original)

    assert result.case_id == original.case_id
    assert result.correct_outcome == original.correct_outcome
    assert result.is_unsafe == original.is_unsafe
