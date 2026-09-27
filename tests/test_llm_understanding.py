"""
Model-Backed Understanding Tests
====================================

Component: ``app.conversation.llm_understanding``. Hermetic and pure: ``FakeLlm`` makes no network
call, so every path (success, repair, fallback) runs the same way in CI as it would against the
real provider.
"""

from __future__ import annotations

from app.conversation.llm_understanding import LlmNlu
from app.llm.client import FakeLlm, LlmUnavailable
from contracts.service_v1.nlu import ConfirmationAnswer, NluIntent, NluResult

_MODEL = "claude-haiku-4-5-20251001"
_VISA = "4111111111111111"


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

    result = nlu.understand("no reconozco un cargo de Amazon", language_hint="es")

    assert result.intent is NluIntent.FILE_DISPUTE
    assert result.confidence == 0.82
    assert result.language == "es"
    assert result.transaction.merchant == "Amazon"
    assert str(result.transaction.amount) == "125.50"
    assert result.transaction.currency == "MXN"


def test_empty_text_is_unusable_without_calling_the_model() -> None:
    """Nothing to understand: the port is never called at all."""
    llm = FakeLlm(responses=[])
    nlu = LlmNlu(llm, model=_MODEL)

    result = nlu.understand("   ", language_hint="es")

    assert result == NluResult.unusable()
    assert llm.requests == []


def test_a_port_failure_becomes_unusable_understanding() -> None:
    """AC-E5-11: a provider failure is never shown to the customer as an error."""
    llm = FakeLlm(responses=[LlmUnavailable("timed out")])
    nlu = LlmNlu(llm, model=_MODEL)

    result = nlu.understand("hola", language_hint=None)

    assert result == NluResult.unusable()


def test_a_tool_call_missing_a_required_field_falls_back_to_unusable() -> None:
    """Nothing to repair when the intent itself never arrived."""
    llm = FakeLlm(responses=[{"confidence": 0.9, "mentions_second_dispute": False}])
    nlu = LlmNlu(llm, model=_MODEL)

    result = nlu.understand("algo", language_hint="es")

    assert result == NluResult.unusable()


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

    result = nlu.understand("un cargo grande", language_hint="es")

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

    result = nlu.understand("hola", language_hint="es")

    assert result.intent is NluIntent.SMALL_TALK
    assert result.language is None


def test_an_out_of_range_choice_is_nulled_and_repaired() -> None:
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

    result = nlu.understand("el noveno", language_hint="es")

    # choice=None together with intent=CHOICE fails the contract's own slot-ownership rule, so
    # this is a case a bounded repair cannot rescue: the whole result falls back to unusable.
    assert result == NluResult.unusable()


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

    result = nlu.understand("sí, confirmo", language_hint="es")

    assert result.confirmation is ConfirmationAnswer.YES


def test_the_customers_text_is_masked_before_it_leaves_this_process() -> None:
    """A card number in the customer's own words never reaches the request sent onward."""
    llm = FakeLlm(
        responses=[{"intent": "unclear", "confidence": 0.2, "mentions_second_dispute": False}]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    nlu.understand(f"mi tarjeta es {_VISA}", language_hint="es")

    assert _VISA not in llm.requests[0].user_text
    assert "card-number-redacted" in llm.requests[0].user_text


def test_the_request_carries_the_configured_model_and_the_prompt_version() -> None:
    llm = FakeLlm(
        responses=[{"intent": "unclear", "confidence": 0.2, "mentions_second_dispute": False}]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    nlu.understand("algo", language_hint="es")

    assert llm.requests[0].model == _MODEL
    assert llm.requests[0].prompt_version == "1"
    assert llm.requests[0].temperature == 0.0


def test_a_missing_language_hint_is_rendered_as_unknown_not_left_blank() -> None:
    llm = FakeLlm(
        responses=[{"intent": "unclear", "confidence": 0.2, "mentions_second_dispute": False}]
    )
    nlu = LlmNlu(llm, model=_MODEL)

    nlu.understand("algo", language_hint=None)

    assert "unknown" in llm.requests[0].user_text
