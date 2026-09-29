"""
Request-Capture PII Tests
==========================

Component: the egress boundary every outbound model request passes through
(``app.conversation.llm_understanding.LlmNlu``, ``app.conversation.model_renderer.LlmRenderer``).
Hermetic: ``FakeLlm``, no network.

Where ``tests/test_masking.py`` proves ``redact_pan`` itself is correct in isolation, this module
proves the guarantee E7 actually asks for: that the *real call path* a customer's message travels
through never lets a card number reach the request the process sends out, and that PII categories
this system's data model excludes everywhere else (a document number, a full email or phone) have
no path to reach a request either, since no contract field ever carries one. This is "the
request-capture fixture" the architecture names (``plan/docs/architecture.md``'s Security and
privacy section) — ``FakeLlm.requests`` already is that fixture; this module is the first to read
it for this purpose.

Limitations
-----------
A document number typed in a customer's free-text message is not detected or redacted today: the
existing card-number detector's Luhn-checksum technique does not extend to a document number,
which carries no checksum. Closing that gap is out of scope for this slice pending a ratified
detection pattern (see the drafted ADR "Document-number-shaped text in outbound model requests and
logs"); this module tests the guarantee the architecture actually supports today, not that gap.
"""

from __future__ import annotations

# Standard libraries
import logging
from datetime import date

# Third-party libraries
import pytest

# Local modules
from app.conversation.llm_understanding import LlmNlu
from app.llm import masking
from app.llm.client import FakeLlm
from contracts.service_v1.nlu import TransactionHint

# A well-known, published Visa test PAN (Luhn-valid) — never a real cardholder's number.
_TEST_CARD_NUMBER = "4111111111111111"
_REFERENCE_DATE = date(2026, 6, 18)


def test_a_card_number_in_the_customers_message_never_reaches_the_outbound_request() -> None:
    """The exact guarantee E7 asks for: capture the real request a customer's message produces
    and assert the raw card digits are not in it, only the fixed placeholder."""
    llm = FakeLlm(
        responses=[{"intent": "file_dispute", "confidence": 0.8, "mentions_second_dispute": False}]
    )
    nlu = LlmNlu(llm, model="claude-sonnet-5")
    message = f"No reconozco un cargo, mi tarjeta es {_TEST_CARD_NUMBER}, ayúdenme por favor."

    nlu.understand(message, language_hint="es", reference_date=_REFERENCE_DATE)

    assert len(llm.requests) == 1
    sent = llm.requests[0].user_text
    assert _TEST_CARD_NUMBER not in sent
    assert masking.PLACEHOLDER in sent


def test_a_card_number_split_by_separators_never_reaches_the_outbound_request() -> None:
    llm = FakeLlm(
        responses=[{"intent": "file_dispute", "confidence": 0.8, "mentions_second_dispute": False}]
    )
    nlu = LlmNlu(llm, model="claude-sonnet-5")
    spaced = "4111 1111 1111 1111"
    message = f"Mi tarjeta {spaced} tiene un cargo que no reconozco."

    nlu.understand(message, language_hint="es", reference_date=_REFERENCE_DATE)

    sent = llm.requests[0].user_text
    assert "1111" * 4 not in sent.replace(" ", "")
    assert masking.PLACEHOLDER in sent


def test_no_contract_field_the_understanding_step_extracts_can_carry_a_document_number() -> None:
    """A document number has no field to travel through even if a customer states one: NluResult's
    TransactionHint holds only merchant, amount, currency, date and a product's last four digits —
    no free-form identifier field a document number could occupy."""
    fields = TransactionHint.model_fields
    assert set(fields) == {
        "merchant",
        "amount",
        "currency",
        "date_on",
        "date_source",
        "product_last4",
    }


def test_no_log_line_in_the_understanding_call_path_ever_carries_the_customers_raw_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A grep-style proof against real emitted log records, not a static source scan: drive a
    scripted call carrying a card number and a plausible document-number-shaped string through the
    real understanding adapter, then grep every captured record for both raw strings."""
    llm = FakeLlm(
        responses=[{"intent": "file_dispute", "confidence": 0.8, "mentions_second_dispute": False}]
    )
    nlu = LlmNlu(llm, model="claude-sonnet-5")
    document_number = "1094921834"
    message = (
        f"Mi cédula es {document_number} y mi tarjeta es {_TEST_CARD_NUMBER}, "
        "no reconozco un cargo."
    )

    with caplog.at_level(logging.DEBUG):
        nlu.understand(message, language_hint="es", reference_date=_REFERENCE_DATE)

    logged_text = "\n".join(record.getMessage() for record in caplog.records)
    assert _TEST_CARD_NUMBER not in logged_text
    assert document_number not in logged_text
    assert message not in logged_text
