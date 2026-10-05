"""
Language Stickiness Tests
=========================

Component: ``app.conversation.language``. Hermetic and pure.
"""

from __future__ import annotations

from app.conversation.language import resolve_language


def test_a_first_message_clearly_in_one_language_sets_it() -> None:
    """The detected language of the first message is the conversation's language."""
    result = resolve_language(current=None, detected="pt")

    assert result.lang == "pt"
    assert result.streak == 0
    assert not result.ambiguous


def test_a_first_message_the_detector_cannot_place_uses_the_best_guess() -> None:
    """An undetectable first message still gets a language, and flags the ambiguity."""
    result = resolve_language(current=None, detected=None)

    assert result.lang == "es"
    assert result.ambiguous


def test_one_off_language_message_does_not_switch() -> None:
    """A single message in another language leaves the conversation where it was."""
    result = resolve_language(current="es", detected="pt", consecutive_off_language=0)

    assert result.lang == "es"
    assert result.streak == 1


def test_two_consecutive_off_language_messages_switch() -> None:
    """The second message in a row in another language switches at once."""
    first = resolve_language(current="es", detected="pt", consecutive_off_language=0)
    second = resolve_language(current="es", detected="pt", consecutive_off_language=first.streak)

    assert second.lang == "pt"
    assert second.streak == 0


def test_an_explicit_request_switches_immediately_even_on_the_first_off_language_message() -> None:
    """Asking to change language switches at once, no streak required."""
    result = resolve_language(current="es", detected="en", explicit_switch_to="en")

    assert result.lang == "en"
    assert result.streak == 0


def test_an_undetectable_message_mid_conversation_does_not_disturb_the_language() -> None:
    """A message that cannot be placed keeps the conversation where it was, streak reset."""
    result = resolve_language(current="es", detected=None, consecutive_off_language=1)

    assert result.lang == "es"
    assert result.streak == 0


def test_a_message_back_in_the_current_language_resets_the_streak() -> None:
    """A returning message in the current language cancels a building streak."""
    result = resolve_language(current="es", detected="es", consecutive_off_language=1)

    assert result.lang == "es"
    assert result.streak == 0


def test_explicit_switch_to_the_current_language_is_a_no_op() -> None:
    """Asking to switch to the language already in use changes nothing."""
    result = resolve_language(current="es", detected="es", explicit_switch_to="es")

    assert result.lang == "es"
    assert result.streak == 0
