"""
Model-Backed Understanding Tests
====================================

Component: ``app.conversation.llm_understanding``. Hermetic and pure: ``FakeLlm`` makes no network
call, so every path (success, repair, fallback) runs the same way in CI as it would against the
real provider.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import pytest

from app.conversation.llm_understanding import LlmNlu
from app.conversation.understanding import TurnAccounting, UnderstandingUnavailable
from app.llm.client import (
    CompletionRequest,
    CompletionResult,
    FakeLlm,
    LlmRequestRejected,
    LlmUnavailable,
)
from contracts.service_v1.envelope import DateSource
from contracts.service_v1.nlu import ConfirmationAnswer, NluIntent, NluResult

_MODEL = "claude-haiku-4-5-20251001"
_VISA = "4111111111111111"
_REFERENCE_DATE = date(2026, 6, 18)


def test_a_well_formed_tool_call_maps_to_a_validated_nlu_result() -> None:
    """The common path: the model's arguments become the contract's own typed result."""
    llm = FakeLlm(
        responses=[
            {
                "intent": "file_dispute",
                "confidence": 0.82,
                "language": "es",
                "merchant": "Amazon",
                "amount": "125.50",
                "currency": "MXN",
                "mentions_second_dispute": False,
            }
        ]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    result, accounting = nlu.understand(
        "no reconozco un cargo de Amazon", language_hint="es", reference_date=_REFERENCE_DATE
    )

    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.confidence == 0.82
    assert result.language == "es"
    assert result.transaction.merchant == "Amazon"
    assert str(result.transaction.amount) == "125.50"
    assert result.transaction.currency == "MXN"
    assert accounting == TurnAccounting(
        model=_MODEL, prompt_version="10", input_tokens=0, output_tokens=0, latency_ms=0.0
    )


def test_empty_text_is_unusable_without_calling_the_model() -> None:
    """Nothing to understand: the port is never called at all."""
    llm = FakeLlm(responses=[])
    nlu = LlmNlu(llm, model=_MODEL)

    result, accounting = nlu.understand("   ", language_hint="es", reference_date=_REFERENCE_DATE)

    assert result == NluResult.unusable()
    assert accounting is None
    assert llm.requests == []


def test_a_transient_port_failure_raises_understanding_unavailable() -> None:
    """A transient failure is not the customer's own ambiguity: it is raised, not folded
    into ``unusable()``, so the caller never spends a clarification-budget attempt on an outage."""
    llm = FakeLlm(responses=[LlmUnavailable("timed out")])
    nlu = LlmNlu(llm, model=_MODEL)

    with pytest.raises(UnderstandingUnavailable):
        nlu.understand("hola", language_hint=None, reference_date=_REFERENCE_DATE)


def test_a_permanent_port_failure_still_becomes_unusable_understanding() -> None:
    """A permanent failure (bad credentials, a malformed request) is never worth retrying and is
    still never shown to the customer as an error — it degrades exactly as it always has."""
    llm = FakeLlm(responses=[LlmRequestRejected("bad credentials")])
    nlu = LlmNlu(llm, model=_MODEL)

    result, accounting = nlu.understand("hola", language_hint=None, reference_date=_REFERENCE_DATE)

    assert result == NluResult.unusable()
    assert accounting is None


def test_a_tool_call_missing_a_required_field_falls_back_to_unusable() -> None:
    """Nothing to repair when the intent itself never arrived."""
    llm = FakeLlm(responses=[{"confidence": 0.9, "mentions_second_dispute": False}])
    nlu = LlmNlu(llm, model=_MODEL)

    result, accounting = nlu.understand("algo", language_hint="es", reference_date=_REFERENCE_DATE)

    assert result == NluResult.unusable()
    assert accounting is not None  # the call itself completed; only the arguments were incomplete


def test_an_overlong_free_text_field_is_truncated_and_repaired() -> None:
    """One bounded repair: a merchant name past the contract's bound is truncated, not discarded."""
    llm = FakeLlm(
        responses=[
            {
                "intent": "file_dispute",
                "confidence": 0.7,
                "merchant": "A" * 200,
                "mentions_second_dispute": False,
            }
        ]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    result, _accounting = nlu.understand(
        "un cargo grande", language_hint="es", reference_date=_REFERENCE_DATE
    )

    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.transaction.merchant == "A" * 80


def test_an_invalid_enum_value_is_nulled_and_repaired() -> None:
    """A language the model spelled wrong is dropped, not treated as an unusable whole result."""
    llm = FakeLlm(
        responses=[
            {
                "intent": "small_talk",
                "confidence": 0.9,
                "language": "not-a-language",
                "mentions_second_dispute": False,
            }
        ]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    result, _accounting = nlu.understand("hola", language_hint="es", reference_date=_REFERENCE_DATE)

    assert result.intent is NluIntent.SMALL_TALK
    assert result.language is None


def test_an_out_of_range_choice_is_dropped_and_the_message_read_as_unclear() -> None:
    llm = FakeLlm(
        responses=[
            {
                "intent": "choice",
                "confidence": 0.8,
                "choice": 9,
                "mentions_second_dispute": False,
            }
        ]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    result, _accounting = nlu.understand(
        "el noveno", language_hint="es", reference_date=_REFERENCE_DATE
    )

    # An unreadable choice leaves the intent without its slot, so it is read as unclear: the
    # customer is asked again rather than the message being thrown away as unusable.
    assert result.intent is NluIntent.UNCLEAR
    assert result.choice is None


def test_a_confirmation_that_belongs_to_its_intent_maps_cleanly() -> None:
    llm = FakeLlm(
        responses=[
            {
                "intent": "confirmation",
                "confidence": 0.95,
                "confirmation": "yes",
                "mentions_second_dispute": False,
            }
        ]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    result, _accounting = nlu.understand(
        "sí, confirmo", language_hint="es", reference_date=_REFERENCE_DATE
    )

    assert result.confirmation is ConfirmationAnswer.YES


def test_the_customers_text_is_masked_before_it_leaves_this_process() -> None:
    """A card number in the customer's own words never reaches the request sent onward."""
    llm = FakeLlm(
        responses=[{"intent": "unclear", "confidence": 0.2, "mentions_second_dispute": False}]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    nlu.understand(f"mi tarjeta es {_VISA}", language_hint="es", reference_date=_REFERENCE_DATE)

    assert _VISA not in llm.requests[0].user_text
    assert "card-number-redacted" in llm.requests[0].user_text


def test_the_request_carries_the_configured_model_and_the_prompt_version() -> None:
    llm = FakeLlm(
        responses=[{"intent": "unclear", "confidence": 0.2, "mentions_second_dispute": False}]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    nlu.understand("algo", language_hint="es", reference_date=_REFERENCE_DATE)

    assert llm.requests[0].model == _MODEL
    assert llm.requests[0].prompt_version == "10"
    assert llm.requests[0].temperature == 0.0


def test_a_missing_language_hint_is_rendered_as_unknown_not_left_blank() -> None:
    llm = FakeLlm(
        responses=[{"intent": "unclear", "confidence": 0.2, "mentions_second_dispute": False}]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    nlu.understand("algo", language_hint=None, reference_date=_REFERENCE_DATE)

    assert "unknown" in llm.requests[0].user_text


def test_a_successful_calls_accounting_matches_the_completions_own_fields() -> None:
    """Every field of ``TurnAccounting`` is a direct copy of the completion's own, not derived."""

    class _FixedResultLlm:
        def complete(self, request: CompletionRequest) -> CompletionResult:
            return CompletionResult(
                tool_input={
                    "intent": "unclear",
                    "confidence": 0.2,
                    "mentions_second_dispute": False,
                },
                model=request.model,
                prompt_version=request.prompt_version,
                input_tokens=120,
                output_tokens=40,
                latency_ms=812.5,
            )

    nlu = LlmNlu(_FixedResultLlm(), model=_MODEL)

    _result, accounting = nlu.understand("algo", language_hint="es", reference_date=_REFERENCE_DATE)

    assert accounting == TurnAccounting(
        model=_MODEL, prompt_version="10", input_tokens=120, output_tokens=40, latency_ms=812.5
    )


def test_a_reported_date_expression_resolves_against_the_reference_date() -> None:
    """End to end through the adapter: the model reports only the customer's own
    words; the resolved date and how it was expressed are what the contract actually carries."""
    llm = FakeLlm(
        responses=[
            {
                "intent": "file_dispute",
                "confidence": 0.8,
                "language": "es",
                "date_expression": "ayer",
                "mentions_second_dispute": False,
            }
        ]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    result, _accounting = nlu.understand(
        "no reconozco un cargo de ayer", language_hint="es", reference_date=_REFERENCE_DATE
    )

    assert result.transaction.date_on == date(2026, 6, 17)
    assert result.transaction.date_source is DateSource.RELATIVE


def test_an_unrecognized_date_expression_leaves_the_transaction_dateless() -> None:
    """The same "nothing stated" outcome as no date at all — a phrase the closed vocabulary
    does not know is never guessed at, matching ``date_expressions.resolve``'s own contract."""
    llm = FakeLlm(
        responses=[
            {
                "intent": "file_dispute",
                "confidence": 0.8,
                "language": "es",
                "date_expression": "hace un tiempo",
                "mentions_second_dispute": False,
            }
        ]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    result, _accounting = nlu.understand(
        "no reconozco un cargo de hace un tiempo",
        language_hint="es",
        reference_date=_REFERENCE_DATE,
    )

    assert result.transaction.date_on is None
    assert result.transaction.date_source is None


def test_an_overlong_date_expression_is_truncated_and_repaired() -> None:
    """The same bounded-repair convention every other free-text field already gets."""
    llm = FakeLlm(
        responses=[
            {
                "intent": "file_dispute",
                "confidence": 0.7,
                "date_expression": "x" * 60,
                "mentions_second_dispute": False,
            }
        ]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    result, _accounting = nlu.understand(
        "un cargo de hace mucho", language_hint="es", reference_date=_REFERENCE_DATE
    )

    # Truncated to 40 characters and then not recognized by the closed vocabulary either way —
    # proven by the fact this does not raise and the transaction stays dateless, the same
    # graceful outcome as any other unrecognized expression.
    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.transaction.date_on is None


def _amount_understanding(amount: object, **extra: object) -> tuple[NluResult, FakeLlm]:
    llm = FakeLlm(
        responses=[
            {
                "intent": "file_dispute",
                "confidence": 0.8,
                "language": "pt",
                "merchant": "Farmacia Salud",
                "amount": amount,
                "currency": "ARS",
                "mentions_second_dispute": False,
                **extra,
            }
        ]
    )
    result, _accounting = LlmNlu(llm, model=_MODEL).understand(
        "nao reconheco a compra", language_hint="pt", reference_date=_REFERENCE_DATE
    )
    return result, llm


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("99.948,89", "99948.89"),
        ("99,948.89", "99948.89"),
        ("99948,89", "99948.89"),
        ("99948.89", "99948.89"),
        ("$ 1 250,50", "1250.50"),
        ("ARS 99948.89", "99948.89"),
        ("$99,948.89 ARS", "99948.89"),
        ("1.234", "1234"),
        ("1,234", "1234"),
        ("1.234.567", "1234567"),
        ("1,234,567", "1234567"),
        ("1.234,5", "1234.5"),
        ("0,5", "0.5"),
        ("125.5", "125.5"),
        ("1234", "1234"),
        ("12 345,67", "12345.67"),
        ("1'234.50", "1234.50"),
        ("R$ 1.234,56", "1234.56"),
        ("1.234,56 €", "1234.56"),
        ("US$ 100", "100"),
        ("\u00a5 1.500", "1500"),
        ("\u00a3 12,50", "12.50"),
        ("ARS$ 5", "5"),
        ("1\u2019250,50", "1250.50"),
        ("1\u00a0250", "1250"),
        ("1\u202f250,50", "1250.50"),
        (",5", "0.5"),
        (" 99.948,89\n", "99948.89"),
        ("MXN 1,250.50", "1250.50"),
        ("COP 1.250.000", "1250000"),
        ("USD 12", "12"),
        ("BRL 5", "5"),
        ("5 EUR", "5"),
        ("500 mxn", "500"),
        ("usd 12", "12"),
        ("Brl 5,50", "5.50"),
        ("1.250,50 eur", "1250.50"),
        ("U$S 100", "100"),
        ("u$s 100", "100"),
        ("100 U$S", "100"),
        ("us$ 100", "100"),
        ("5 pen", "5"),
    ],
)
def test_a_localized_amount_is_read_with_its_own_separators(spoken: str, expected: str) -> None:
    """A figure written either way round (or with a symbol or ISO code) keeps its value."""
    result, _llm = _amount_understanding(spoken)

    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.transaction.amount == Decimal(expected)
    assert result.transaction.merchant == "Farmacia Salud"


@pytest.mark.parametrize(
    "spoken",
    [
        "abc",
        "12.3.4",
        "1.2345.678",
        "1,2.3,4",
        "$",
        "0.125",
        "1" * 15,
        ",123,456",
        "1234,567,890",
        "1234.567,89",
        "1,23,456",
        "1.234,56.78",
        "12 50",
        "1 2",
        "-50",
        "1e5",
        "100 y 200",
        "25% de 1.000",
        "3x 1.500,00",
        "12abc",
        "1.234'567,89",
        "5 MIL",
        "MIL 5",
        "PIX 50",
        "12ABC",
        "mil 5",
        "pix 50",
        "5 dolares",
        "U$S",
        "U$X 100",
        "usd usd 5",
        "100 u$s 5",
        "u\u017fd 5",
        "AR\u017f 5",
        "N\u0130O 5",
        "n\u0131o 5",
    ],
)
def test_an_unparseable_amount_is_dropped_while_the_rest_of_the_understanding_survives(
    spoken: str,
) -> None:
    """The figure is the only thing lost: merchant, currency and intent still reach the dialogue,
    so one garbled amount never turns a clear request into an unusable turn."""
    result, llm = _amount_understanding(spoken)

    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.transaction.amount is None
    assert result.transaction.merchant == "Farmacia Salud"
    assert result.transaction.currency == "ARS"
    assert len(llm.requests) == 1


@pytest.mark.parametrize(
    ("sent", "expected"), [(99.5, "99.5"), (100, "100"), (0.5, "0.5"), (12.0, "12.0")]
)
def test_an_amount_sent_as_a_json_number_is_read_like_one_sent_as_text(
    sent: object, expected: str
) -> None:
    result, llm = _amount_understanding(sent)

    assert result.transaction.amount == Decimal(expected)
    assert result.transaction.merchant == "Farmacia Salud"
    assert len(llm.requests) == 1


@pytest.mark.parametrize("sent", [True, [99.5], {"value": 1}, 1e16, 0.30000000000000004, 1e-05])
def test_an_amount_that_is_not_an_amount_is_dropped_whatever_json_type_it_has(
    sent: object,
) -> None:
    result, _llm = _amount_understanding(sent)

    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.transaction.amount is None
    assert result.transaction.merchant == "Farmacia Salud"
    assert result.transaction.currency == "ARS"


def test_a_valid_localized_amount_survives_the_repair_of_another_field() -> None:
    result, llm = _amount_understanding("99.948,89", merchant="F" * 100)

    assert result.transaction.amount == Decimal("99948.89")
    assert result.transaction.merchant == "F" * 80
    assert len(llm.requests) == 1


def test_the_exact_localized_call_from_a_portuguese_report_is_understood() -> None:
    """The model's arguments for "Farmacia Salud, 21 de abril, 99.948,89 ARS", verbatim."""
    llm = FakeLlm(
        responses=[
            {
                "intent": "file_dispute",
                "confidence": 0.9,
                "language": "pt",
                "merchant": "Farmacia Salud",
                "date_expression": "21 de abril",
                "amount": "99.948,89",
                "currency": "ARS",
                "mentions_second_dispute": False,
            }
        ]
    )

    result, _accounting = LlmNlu(llm, model=_MODEL).understand(
        "Foi a compra na Farmacia Salud de 21 de abril, 99.948,89 ARS",
        language_hint="pt",
        reference_date=_REFERENCE_DATE,
    )

    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.language == "pt"
    assert result.transaction.merchant == "Farmacia Salud"
    assert result.transaction.amount == Decimal("99948.89")
    assert result.transaction.currency == "ARS"


def test_the_amount_description_asks_for_plain_decimal_text() -> None:
    """The model is told the format the parser reads best, so the repair is the exception."""
    result, llm = _amount_understanding("1.00")
    assert result.transaction.amount == Decimal("1.00")

    properties = llm.requests[0].tool.input_schema["properties"]
    assert isinstance(properties, dict)
    assert "no thousands separator" in str(properties["amount"])


def _understand(extraction: dict[str, object], text: str = "hola") -> NluResult:
    arguments = {"intent": "file_dispute", "confidence": 0.9, "mentions_second_dispute": False}
    arguments.update(extraction)
    result, _accounting = LlmNlu(FakeLlm(responses=[arguments]), model=_MODEL).understand(
        text, language_hint="es", reference_date=_REFERENCE_DATE
    )
    return result


@pytest.mark.parametrize(
    "text",
    [
        "Não reconheço $2.763,79 do dia 15 de abril.",
        "I don't recognize a charge of $2,763.79.",
        "No reconozco un cobro de $ 2.763,79.",
    ],
    ids=["pt", "en", "es"],
)
def test_a_currency_guessed_from_a_bare_dollar_sign_is_dropped(text: str) -> None:
    result = _understand({"amount": "2763.79", "currency": "BRL"}, text)

    assert result.transaction.currency is None
    assert result.transaction.amount == Decimal("2763.79")


@pytest.mark.parametrize(
    ("text", "currency"),
    [
        ("Não reconheço R$ 2.763,79.", "BRL"),
        ("I don't recognize US$2,763.79.", "USD"),
        ("No reconozco COL$ 2.763,79.", "COP"),
        ("No reconozco MX$ 2.763,79.", "MXN"),
        ("No reconozco AR$ 2.763,79.", "ARS"),
        ("No reconozco CL$ 2.763,79.", "CLP"),
        ("No reconozco C$ 2.763,79.", "NIO"),
        ("I don't recognize a charge of $2,763.79 USD.", "USD"),
        ("No reconozco $ 2.763,79 COP.", "COP"),
        ("No reconozco $ 2.763,79 dólares.", "USD"),
        ("No reconozco $ 2.763,79 dolares.", "USD"),
        ("I don't recognize $2,763.79 dollars.", "USD"),
        ("I don't recognize $2,763.79 dollar.", "USD"),
        ("Não reconheço $ 2.763,79 reais.", "BRL"),
        ("No reconozco $ 2.763,79 pesos colombianos.", "COP"),
        ("No reconozco $ 2.763,79 peso mexicano.", "MXN"),
        ("I don't recognize $2,763.79 Colombian pesos.", "COP"),
        ("I don't recognize $2,763.79 Mexican pesos.", "MXN"),
        ("I don't recognize $2,763.79 Argentine pesos.", "ARS"),
        ("I don't recognize $2,763.79 Chilean pesos.", "CLP"),
        ("I don't recognize $2,763.79 Uruguayan peso.", "UYU"),
        ("No reconozco $ 2.763,79 euros.", "EUR"),
        ("I don't recognize $2,763.79 in euro.", "EUR"),
        ("I don't recognize €2,763.79 or $3.", "EUR"),
        ("I don't recognize £2,763.79 or $3.", "GBP"),
        ("I don't recognize ¥2,763.79 or $3.", "JPY"),
    ],
    ids=[
        "pt-reais-prefix",
        "en-us-prefix",
        "es-col-prefix",
        "es-mx-prefix",
        "es-ar-prefix",
        "es-cl-prefix",
        "es-c-prefix",
        "en-code-usd",
        "es-code-cop",
        "es-dolares",
        "es-dolares-unaccented",
        "en-dollars",
        "en-dollar",
        "pt-reais",
        "es-pesos-colombianos",
        "es-peso-mexicano",
        "en-colombian-pesos",
        "en-mexican-pesos",
        "en-argentine-pesos",
        "en-chilean-pesos",
        "en-uruguayan-peso",
        "es-euros",
        "en-euro",
        "euro-sign",
        "pound-sign",
        "yen-sign",
    ],
)
def test_a_currency_the_message_states_is_kept_beside_a_dollar_sign(
    text: str, currency: str
) -> None:
    result = _understand({"amount": "2763.79", "currency": currency}, text)

    assert result.transaction.currency == currency


@pytest.mark.parametrize(
    "text",
    [
        "No reconozco $ 2.763,79 pesos.",
        "No reconozco $ 2.763,79 peso.",
        "I don't recognize $2,763.79 pesos.",
    ],
)
def test_the_word_pesos_alone_is_not_a_stated_currency(text: str) -> None:
    result = _understand({"amount": "2763.79", "currency": "COP"}, text)

    assert result.transaction.currency is None
    assert result.transaction.amount == Decimal("2763.79")


def test_a_currency_is_kept_when_the_message_has_no_dollar_sign() -> None:
    result = _understand({"amount": "2763.79", "currency": "COP"}, "No reconozco 2763.79")

    assert result.transaction.currency == "COP"


def test_the_word_real_is_not_a_stated_currency() -> None:
    result = _understand(
        {"amount": "50", "currency": "BRL"}, "I have a real problem with a charge of $50."
    )

    assert result.transaction.currency is None


def test_a_lowercase_currency_code_is_read_in_capitals() -> None:
    result = _understand({"merchant": "Cine Premium", "amount": "1914215", "currency": "cop"})

    assert result.transaction.currency == "COP"
    assert result.transaction.amount == Decimal("1914215")
    assert result.transaction.merchant == "Cine Premium"


def test_a_currency_that_is_not_a_code_is_dropped_and_the_rest_is_kept() -> None:
    result = _understand({"merchant": "Cine Premium", "amount": "1914215", "currency": "pesos"})

    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.transaction.currency is None
    assert result.transaction.merchant == "Cine Premium"
    assert result.transaction.amount == Decimal("1914215")


@pytest.mark.parametrize("absent", ["", "   ", "null", "None", "NULL"])
def test_an_optional_field_sent_empty_or_as_null_is_read_as_absent(absent: str) -> None:
    result = _understand(
        {
            "merchant": "Cine Premium",
            "date_expression": absent,
            "currency": absent,
            "category": absent,
            "detail": absent,
            "product_last4": absent,
            "language": absent,
        }
    )

    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.transaction.merchant == "Cine Premium"
    assert result.transaction.date_on is None
    assert result.transaction.currency is None
    assert result.transaction.product_last4 is None
    assert result.category is None
    assert result.detail is None
    assert result.language is None


def test_control_characters_in_a_field_are_read_as_spaces() -> None:
    result = _understand({"merchant": "Cine\x00 Premium\x07"})

    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.transaction.merchant == "Cine  Premium"


def test_a_malformed_last_four_digits_are_dropped_and_the_rest_is_kept() -> None:
    result = _understand({"merchant": "Cine Premium", "product_last4": "12"})

    assert result.transaction.product_last4 is None
    assert result.transaction.merchant == "Cine Premium"


def test_a_stray_choice_under_another_intent_is_dropped_and_the_transaction_kept() -> None:
    result = _understand(
        {"merchant": "Cine Premium", "amount": "1914215", "currency": "COP", "choice": 3}
    )

    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.choice is None
    assert result.transaction.merchant == "Cine Premium"
    assert result.transaction.amount == Decimal("1914215")


def test_a_stray_confirmation_and_requested_language_are_dropped_the_same_way() -> None:
    result = _understand(
        {"merchant": "Cine Premium", "confirmation": "yes", "requested_language": "en"}
    )

    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.confirmation is None
    assert result.requested_language is None
    assert result.transaction.merchant == "Cine Premium"


@pytest.mark.parametrize("intent", ["confirmation", "choice", "switch_language"])
def test_an_intent_reported_without_its_slot_is_read_as_unclear_keeping_the_transaction(
    intent: str,
) -> None:
    result = _understand({"intent": intent, "merchant": "Cine Premium", "amount": "1914215"})

    assert result.intent is NluIntent.UNCLEAR
    assert result.confidence == 0.0
    assert result.transaction.merchant == "Cine Premium"
    assert result.transaction.amount == Decimal("1914215")


def test_an_intent_with_its_own_slot_is_left_as_reported() -> None:
    result = _understand({"intent": "choice", "choice": 2, "currency": "cop"})

    assert result.intent is NluIntent.CHOICE
    assert result.choice == 2
    assert result.transaction.currency == "COP"


def test_a_discarded_result_logs_the_failing_fields_and_never_the_customers_words(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "mi-numero-4111111111111111"
    with caplog.at_level(logging.WARNING, logger="app.conversation.llm_understanding"):
        result = _understand({"confidence": secret, "merchant": secret}, text=secret)

    assert result == NluResult.unusable()
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "understanding_discarded causes=confidence:float_parsing" in messages
    assert secret not in messages
    assert "4111" not in messages


def test_a_failure_inside_the_transaction_logs_its_path_and_never_its_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "4111111111111111"
    with caplog.at_level(logging.WARNING, logger="app.conversation.llm_understanding"):
        result = _understand({"intent": secret, "product_last4": secret, "merchant": secret})

    assert result == NluResult.unusable()
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "understanding_discarded causes=intent:" in messages
    assert secret not in messages


def test_a_merchant_named_none_or_null_survives_the_repair() -> None:
    result = _understand({"merchant": "Null", "currency": "cop"})

    assert result.transaction.merchant == "Null"
    assert result.transaction.currency == "COP"


def test_a_number_sent_where_text_belongs_is_read_as_its_text() -> None:
    result = _understand({"merchant": 5, "product_last4": 4417})

    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.transaction.merchant == "5"
    assert result.transaction.product_last4 == "4417"


def test_a_usable_result_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.conversation.llm_understanding"):
        _understand({"merchant": "Cine Premium", "choice": 3})

    assert caplog.records == []
