"""
Fake Understanding Tests
========================

Component: ``app.conversation.understanding``. Hermetic: keyword matching only, no network.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.conversation.understanding import FakeNlu
from contracts.service_v1.envelope import Lang
from contracts.service_v1.nlu import ConfirmationAnswer, NluIntent, NluResult

_NLU = FakeNlu()
_REFERENCE_DATE = date(2026, 6, 18)


def _understand(text: str, *, language_hint: Lang | None = None) -> NluResult:
    """``FakeNlu.understand`` never produces accounting; tests here only need the result."""
    result, accounting = _NLU.understand(
        text, language_hint=language_hint, reference_date=_REFERENCE_DATE
    )
    assert accounting is None
    return result


def test_empty_text_is_unusable() -> None:
    """Blank input is treated the same as output that failed to validate."""
    result = _understand("   ")

    assert result == NluResult.unusable()


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("quiero disputar un cargo que no reconozco", NluIntent.FILE_DISPUTE),
        ("quero contestar uma cobrança", NluIntent.FILE_DISPUTE),
        ("clonaron mi tarjeta", NluIntent.REPORT_FRAUD),
        ("não reconheço, é fraude", NluIntent.REPORT_FRAUD),
        ("perdí mi tarjeta", NluIntent.REPORT_CARD_LOSS),
        ("perdi meu cartão", NluIntent.REPORT_CARD_LOSS),
        ("quiero hablar con un asesor", NluIntent.REQUEST_PERSON),
        ("quero falar com um atendente", NluIntent.REQUEST_PERSON),
        ("devuélvanme el dinero", NluIntent.REQUEST_REVERSAL),
        ("quiero transferir 500 a mi hermano", NluIntent.UNSUPPORTED_ACTION),
        ("quero aumentar meu limite", NluIntent.UNSUPPORTED_ACTION),
        ("¿cuánto tiempo tengo para disputar?", NluIntent.POLICY_QUESTION),
        ("qual o prazo para contestar?", NluIntent.POLICY_QUESTION),
        ("quiero saber de mi disputa", NluIntent.DISPUTE_STATUS),
        ("hola", NluIntent.SMALL_TALK),
        ("adiós, gracias", NluIntent.FAREWELL),
        ("tchau, obrigado", NluIntent.FAREWELL),
        ("asdkjhaskjdh", NluIntent.UNCLEAR),
    ],
)
def test_keyword_rules_classify_the_expected_intent(text: str, intent: NluIntent) -> None:
    """Each scripted phrase is read as the intent it names."""
    result = _understand(text, language_hint=None)

    assert result.intent is intent


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        ("sí", ConfirmationAnswer.YES),
        ("confirmo", ConfirmationAnswer.YES),
        ("sim", ConfirmationAnswer.YES),
        ("yes", ConfirmationAnswer.YES),
        ("no", ConfirmationAnswer.NO),
        ("não", ConfirmationAnswer.NO),
        ("ok", ConfirmationAnswer.AMBIGUOUS),
        ("vale", ConfirmationAnswer.AMBIGUOUS),
    ],
)
def test_confirmation_answers_are_read_from_a_closed_set(
    text: str, answer: ConfirmationAnswer
) -> None:
    """A bare yes, no or ambiguous acknowledgement are told apart."""
    result = _understand(text, language_hint=None)

    assert result.intent is NluIntent.CONFIRMATION
    assert result.confirmation is answer


def test_a_yes_with_a_change_is_not_read_as_a_bare_confirmation() -> None:
    """'sí, pero...' is not the clean yes the bare pattern matches."""
    result = _understand("sí, pero cambien la fecha", language_hint=None)

    assert result.confirmation is None


def test_a_switch_language_request_names_the_requested_language() -> None:
    """An explicit request to change language is told apart from merely mentioning one."""
    result = _understand("¿podemos hablar en portugués?", language_hint=None)

    assert result.intent is NluIntent.SWITCH_LANGUAGE
    assert result.requested_language == "pt"


def test_mentioning_a_language_without_asking_to_switch_is_not_a_switch_request() -> None:
    """Naming a language is not the same as asking to change to it."""
    result = _understand("mi tarjeta es de crédito, en español por favor", language_hint=None)

    assert result.intent is not NluIntent.SWITCH_LANGUAGE


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("hola, quiero disputar un cargo", "es"),
        ("olá, quero contestar uma cobrança", "pt"),
        ("hello, I want to dispute a charge", "en"),
    ],
)
def test_the_language_is_detected_from_keywords(text: str, language: str) -> None:
    """Each language's own vocabulary is enough to identify it."""
    result = _understand(text, language_hint=None)

    assert result.language == language


def test_a_language_hint_breaks_a_tie_when_nothing_is_detected() -> None:
    """With no keyword match at all, the hint carries the language forward."""
    result = _understand("123456", language_hint="pt")

    assert result.language == "pt"


def test_a_policy_question_carries_the_query_text() -> None:
    """The policy query is available for the retrieval step to search with."""
    result = _understand(
        "¿cuánto tiempo tengo para disputar un cargo duplicado?", language_hint=None
    )

    assert result.policy_query is not None
    assert "cuánto tiempo" in result.policy_query
