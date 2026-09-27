"""
Masking Serializer Tests
=========================

Component: ``app.llm.masking``. Hermetic and pure: no network, no clock.

An adversarial set (separator variants, adjacent non-digit characters, multiple card-like runs in
one field) and a false-positive set (order numbers, phone numbers, reference codes of card-like
length) both pass with the detector applied at this boundary. Every card number below is a
well-known test number (Visa/Mastercard/Amex/Discover's published test PANs); every false-positive
number below was generated and checked to contain no Luhn-valid window of card length anywhere in
it, so a genuine detector improvement is what would make these tests fail, not a coincidence of
the chosen digits.
"""

from __future__ import annotations

import random

import pytest

from app.llm.masking import _MAX_PAN_DIGITS, _MIN_PAN_DIGITS, PLACEHOLDER, redact_pan

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
    """Every separator a card number is typed with, plus none at all, is caught."""
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


def test_two_card_like_runs_separated_by_only_a_single_space_are_fully_redacted() -> None:
    """The pathological case: nothing but one allowed separator sits between two full PANs. These
    two particular test PANs also contain a Luhn-valid window that straddles the boundary between
    them, so leaving two separate placeholders would still expose that spanning window's own
    digits; a correct detector merges the whole thing into one redacted span instead."""
    text = f"{_VISA} {_MASTERCARD}"

    result = redact_pan(text)

    assert PLACEHOLDER in result.masked
    assert _VISA not in result.masked
    assert _MASTERCARD not in result.masked
    assert not any(char.isdigit() for char in result.masked)


# A separate, from-scratch Luhn check used only to build the randomized test below: it must not
# share any code with the detector, so a bug shared between generator and detector cannot hide.
def _oracle_luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _random_luhn_valid_number(length: int, rng: random.Random) -> str:
    """An independently generated, Luhn-valid digit string of ``length`` digits."""
    payload = "".join(str(rng.randint(0, 9)) for _ in range(length - 1))
    check_total = 0
    for index, char in enumerate(reversed(payload)):
        value = int(char)
        if index % 2 == 0:  # lands at an odd, doubled position once the check digit follows it
            value *= 2
            if value > 9:
                value -= 9
        check_total += value
    check_digit = str((10 - check_total % 10) % 10)
    return payload + check_digit


def _any_luhn_valid_pan_window_survives(masked: str) -> bool:
    """Whether a full 13-to-19-digit Luhn-valid window can still be read out of ``masked``."""
    digits_only = "".join(char for char in masked if char.isdigit())
    for start in range(len(digits_only)):
        for length in range(_MIN_PAN_DIGITS, _MAX_PAN_DIGITS + 1):
            end = start + length
            if end > len(digits_only):
                break
            if _oracle_luhn_valid(digits_only[start:end]):
                return True
    return False


_ADJACENT_PAIR_SEPARATORS = ("", " ", "-", ",", "_", ".")


def test_two_adjacent_independent_luhn_valid_pans_never_leave_a_readable_window() -> None:
    """Randomized adversarial case: two independently Luhn-valid card numbers placed back to back
    with zero or one separator between them must never leave a full card-length Luhn-valid window
    readable, even when a window straddling the boundary between the two also happens to pass the
    checksum: a scan that only takes the first or longest valid window at each start position and
    then resumes past it can leave part of the neighboring number's own digits uncovered."""
    rng = random.Random(20260927)  # noqa: S311 -- test data generation, not cryptographic use

    for _ in range(1000):
        first = _random_luhn_valid_number(rng.randint(_MIN_PAN_DIGITS, _MAX_PAN_DIGITS), rng)
        second = _random_luhn_valid_number(rng.randint(_MIN_PAN_DIGITS, _MAX_PAN_DIGITS), rng)
        separator = rng.choice(_ADJACENT_PAIR_SEPARATORS)
        text = f"card one {first}{separator}{second} card two"

        result = redact_pan(text)

        assert not _any_luhn_valid_pan_window_survives(result.masked)


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
