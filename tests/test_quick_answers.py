"""
Quick Answers Tests
===================

Component: ``app.conversation.quick_answers``. The texts the chat's buttons send are read from the
web's own contract, so a button whose text the dialogue no longer recognises fails here rather than
in front of a customer. The labels the buttons display are covered too, for a customer who types
what they see.
"""

from __future__ import annotations

# Third-party libraries
import pytest

# Local modules
from app.conversation.quick_answers import read_quick_answer
from contracts.service_v1.nlu import ConfirmationAnswer
from tests.web_labels import web_label, web_sent_text

_LANGUAGES = ("es", "pt", "en")


def test_the_text_the_confirm_button_sends_is_a_yes() -> None:
    assert read_quick_answer(web_sent_text("CONFIRMATION_TEXT")) is ConfirmationAnswer.YES


def test_the_text_the_decline_button_sends_is_a_no() -> None:
    assert read_quick_answer(web_sent_text("DECLINE_TEXT")) is ConfirmationAnswer.NO


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_confirm_label_is_a_yes(language: str) -> None:
    assert read_quick_answer(web_label(language, "chat.confirm")) is ConfirmationAnswer.YES


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_decline_label_is_a_no(language: str) -> None:
    assert read_quick_answer(web_label(language, "chat.decline")) is ConfirmationAnswer.NO


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("yes", ConfirmationAnswer.YES),
        ("no", ConfirmationAnswer.NO),
        ("Sí", ConfirmationAnswer.YES),
        ("SI.", ConfirmationAnswer.YES),
        ("  No!  ", ConfirmationAnswer.NO),
        ("Sim", ConfirmationAnswer.YES),
        ("Não", ConfirmationAnswer.NO),
        ("nao obrigada", ConfirmationAnswer.NO),
        ("No, don\u2019t file", ConfirmationAnswer.NO),
        ("no, gracias", ConfirmationAnswer.NO),
        ("Sí, por favor", ConfirmationAnswer.YES),
    ],
)
def test_short_forms_are_read_without_regard_to_case_accents_or_punctuation(
    text: str, expected: ConfirmationAnswer
) -> None:
    assert read_quick_answer(text) is expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "no, no reconozco ese cargo",
        "no, la otra transacción",
        "sí, pero por 500",
        "sim, mas o valor está errado",
        "yes but for a different amount",
        "no la registres todavía, antes quiero revisar",
        "Sí, registrar el cargo de Amazon",
        "maybe",
        "123",
    ],
)
def test_longer_or_mixed_messages_are_left_to_the_understanding_step(text: str) -> None:
    assert read_quick_answer(text) is None
