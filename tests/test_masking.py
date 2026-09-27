"""
Masking Serializer Tests
=========================

Component: ``app.llm.masking``. Hermetic and pure: no network, no clock.

thot-follow-up issue #80: an adversarial set (separator variants, adjacent non-digit characters,
multiple card-like runs in one field) and a false-positive set (order numbers, phone numbers,
reference codes of card-like length) both pass with the detector applied at this boundary. Every
card number below is a well-known test number (Visa/Mastercard/Amex/Discover's published test
PANs); every false-positive number below was generated and checked to contain no Luhn-valid
window of card length anywhere in it, so a genuine detector improvement is what would make these
tests fail, not a coincidence of the chosen digits.
"""

from __future__ import annotations

import pytest

from app.llm.masking import PLACEHOLDER, redact_pan

# Well-known test PANs (Luhn-valid), one per major network and digit length.
_VISA = "4111111111111111"
_MASTERCARD = "5500000000000004"
_AMEX = "340000000000009"
_DISCOVER = "6011111111111117"


@pytest.mark.parametrize(
    "text",
    [
        _VISA,
        "4111-1111-1111-1111",
        "4111 1111 1111 1111",
        "4111,1111,1111,1111",
        "4111_1111_1111_1111",
        "4111.1111.1111.1111",
        _AMEX,
        _MASTERCARD,
        _DISCOVER,
    ],
    ids=[
        "no_separator",
        "hyphen",
        "space",
        "comma",
        "underscore",
        "period",
        "amex_15_digits",
        "mastercard",
        "discover",
    ],
)
def test_a_card_shaped_run_is_redacted_whatever_its_separator(text: str) -> None:
    """Every separator variant issue #80 names, plus none at all, is caught."""
    digits_only = "".join(char for char in text if char.isdigit())

    result = redact_pan(text)

    assert result.found is True
    assert PLACEHOLDER in result.masked
    assert digits_only not in result.masked


@pytest.mark.parametrize(
    "text",
    [
        f"card{_VISA}end",
        f"x{_VISA}y",
        f"tarjeta:{_VISA}.",
        f"({_VISA})",
    ],
    ids=["letters_both_sides", "single_letters", "colon_and_period", "parentheses"],
)
def test_adjacent_non_digit_characters_do_not_hide_the_run(text: str) -> None:
    """A card number is still found when non-digit characters sit right against it."""
    result = redact_pan(text)

    assert result.found is True
    assert PLACEHOLDER in result.masked


def test_two_card_like_runs_in_one_field_are_both_redacted() -> None:
    """Two separate, clearly delimited card numbers in the same message both disappear."""
    text = f"My card is {_VISA} and my backup card is {_MASTERCARD}, please use either."

    result = redact_pan(text)

    assert result.masked.count(PLACEHOLDER) == 2
    assert _VISA not in result.masked
    assert _MASTERCARD not in result.masked


def test_two_card_like_runs_separated_by_only_a_single_space_are_both_redacted() -> None:
    """The pathological case: nothing but one allowed separator sits between two full PANs."""
    text = f"{_VISA} {_MASTERCARD}"

    result = redact_pan(text)

    assert result.masked.count(PLACEHOLDER) == 2
    assert _VISA not in result.masked
    assert _MASTERCARD not in result.masked


# Generated digit strings, verified offline to contain no Luhn-valid window of length 13 to 19
# anywhere within them, so they stand in for order numbers, phone numbers and reference codes of
# card-like length without accidentally also being card-shaped.
_NON_CARD_DIGITS = {
    13: "1043321819600",
    14: "13389083863794",
    15: "026542351161559",
    16: "4131647525534192",
    17: "95376724238849696",
    18: "039117182278248963",
    19: "6763201632870831727",
}


@pytest.mark.parametrize("length", sorted(_NON_CARD_DIGITS))
def test_a_digit_run_of_card_like_length_that_fails_luhn_is_left_alone(length: int) -> None:
    """Length alone never triggers redaction: the false-positive set stays untouched."""
    digits = _NON_CARD_DIGITS[length]

    result = redact_pan(f"reference number {digits} for your records")

    assert result.found is False
    assert result.masked == f"reference number {digits} for your records"


def test_an_ordinary_phone_number_is_left_alone() -> None:
    """A phone number, with or without a country code, is well under the PAN length floor."""
    text = "call me at +57 300 123 4567 tomorrow"

    result = redact_pan(text)

    assert result.found is False
    assert result.masked == text


def test_a_digit_run_below_thirteen_digits_is_never_redacted() -> None:
    """Below the shortest real PAN length, nothing is ever flagged."""
    result = redact_pan("case number 123456789012")

    assert result.found is False


def test_text_with_no_digits_is_returned_unchanged() -> None:
    """The common case: ordinary text with no digit run at all."""
    text = "quiero disputar un cargo que no reconozco"

    result = redact_pan(text)

    assert result.found is False
    assert result.masked == text


def test_empty_text_is_returned_unchanged() -> None:
    """The empty string is its own well-defined case, not an error."""
    result = redact_pan("")

    assert result.found is False
    assert result.masked == ""


def test_only_the_matched_span_is_replaced_surrounding_text_is_untouched() -> None:
    """Redaction never widens beyond the digits (and their internal separators) it matched."""
    text = f"Hola, mi tarjeta {_VISA} tuvo un cargo el mes pasado."

    result = redact_pan(text)

    assert result.masked == f"Hola, mi tarjeta {PLACEHOLDER} tuvo un cargo el mes pasado."
