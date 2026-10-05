"""
Request-Capture PII Tests
==========================

Component: the egress boundary the understanding step's outbound model request passes through
(``app.conversation.llm_understanding.LlmNlu``). Hermetic: ``FakeLlm``, no network.

Where ``tests/test_masking.py`` proves ``redact_pan`` itself is correct in isolation, this module
proves two guarantees on the *real call path* a customer's message travels through: a card number
never reaches the request the process sends out, and no log line that path emits carries the
customer's raw text. It also pins the shape of the structured transaction hint the step extracts:
the hint has no document-number, email or phone field. ``FakeLlm.requests`` is the request-capture
fixture the tests read.

Limitations
-----------
A document number typed in a customer's free-text message is not detected or redacted: the
card-number detector's Luhn-checksum technique does not extend to a document number, which carries
no checksum, so the value still reaches the outbound request unmasked. The conversation never asks
for one and never echoes the free-text fields back (see "Security posture" in
``docs/limitations.md``). The log test below uses a document number only to prove it is not
logged; no test here claims the request is free of it. Only the understanding step's request is
captured here; the renderer's request is not.
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
    """Capture the real request a customer's message produces and assert the raw card digits are
    not in it, only the fixed placeholder."""
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


def test_the_transaction_hint_has_no_dedicated_identifier_field() -> None:
    """The structured transaction hint has no identifier field: it holds only merchant, amount,
    currency, date and a product's last four digits. The merchant is free text and can hold
    whatever a customer typed (see Limitations); this pins that no dedicated identifier field
    exists."""
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
