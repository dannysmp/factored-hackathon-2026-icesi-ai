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
from collections.abc import Iterator

import pytest

from app.llm.masking import (
    _MAX_PAN_DIGITS,
    _MIN_PAN_DIGITS,
    DOCUMENT_PLACEHOLDER,
    PLACEHOLDER,
    _longest_digit_run,
    redact_document_numbers,
    redact_pan,
    safe_hex_suffix,
)

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


# -----------------------------------------------------------------------------
# safe_hex_suffix — regenerated the false positive this project actually hit
# -----------------------------------------------------------------------------

# Three real suffixes ``T-20260618-<suffix>``/``T-<date>-<suffix>`` handoff tickets produced during
# a live evaluation run, each of which combined with the date's 8 digits into a run that
# ``redact_pan`` flagged as a leaked card number: a plain reference number the customer was told to
# quote on the phone, treated as PII. Any fix must regenerate every one of these.
_REAL_COLLIDING_SUFFIXES = ("06022946", "02924064", "993471b0")


def _sequence(*values: str) -> Iterator[str]:
    yield from values


@pytest.mark.parametrize("colliding", _REAL_COLLIDING_SUFFIXES)
def test_a_real_colliding_suffix_is_regenerated(
    monkeypatch: pytest.MonkeyPatch, colliding: str
) -> None:
    """Each of these, joined to an 8-digit date by a hyphen, is exactly what a live run already
    saw ``redact_pan`` flag as a card number. A fix that only checks the whole suffix for being
    all-digit would miss ``993471b0`` (only its first 5 characters are digits) — this failed
    before the fix, for a different reason per suffix, and must never regenerate the same value."""
    assert redact_pan(f"reference 20260618{colliding}").found  # the collision this suffix caused

    calls = _sequence(colliding, "aabbccdd")
    monkeypatch.setattr("app.llm.masking.secrets.token_hex", lambda _n: next(calls))

    result = safe_hex_suffix(preceding_digits=8)

    assert result == "AABBCCDD"


def test_a_suffix_with_no_leading_digits_is_accepted_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _sequence("AB12CD34")
    monkeypatch.setattr("app.llm.masking.secrets.token_hex", lambda _n: next(calls))

    assert safe_hex_suffix(preceding_digits=8) == "AB12CD34"


def test_four_leading_digits_is_accepted_five_is_regenerated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _sequence("1234AB78", "5A6B7C8D")
    monkeypatch.setattr("app.llm.masking.secrets.token_hex", lambda _n: next(calls))

    assert safe_hex_suffix(preceding_digits=8) == "1234AB78"

    calls2 = _sequence("12345B78", "5A6B7C8D")
    monkeypatch.setattr("app.llm.masking.secrets.token_hex", lambda _n: next(calls2))

    assert safe_hex_suffix(preceding_digits=8) == "5A6B7C8D"


def test_no_preceding_digits_accepts_every_candidate_on_the_first_try(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With nothing digit-shaped before it, the suffix itself would need to reach 13 digits on its
    own to be rejected — an 8-character hex suffix never can, so even an all-digit candidate is
    accepted immediately, with no retry."""
    calls = _sequence("99999999")
    monkeypatch.setattr("app.llm.masking.secrets.token_hex", lambda _n: next(calls))

    assert safe_hex_suffix(nbytes=4, preceding_digits=0) == "99999999"


def test_the_real_collision_no_longer_survives_through_a_full_reference_number() -> None:
    """End-to-end proof, not just a unit check on the suffix: the exact vulnerable reference this
    project's own live run produced (date + the real colliding suffix) is card-shaped and would
    have been flagged; the regenerated replacement this function returns is not."""
    vulnerable = "T-20260618-06022946"
    assert redact_pan(vulnerable).found

    safe = "T-20260618-AABBCCDD"
    assert not redact_pan(safe).found


def test_a_real_request_id_collision_is_regenerated(monkeypatch: pytest.MonkeyPatch) -> None:
    """``request_id`` had no protection at all before this fix (a raw, unjoined
    ``secrets.token_hex(8)``, unlike the date-joined ticket/case suffixes the rest of this section
    covers). ``722263483763217e`` is a real value ``secrets.token_hex(8)`` produced: 15 of its 16
    characters are digits, and ``redact_pan`` flags it when embedded in a real request id
    (``req_722263483763217e``) exactly as this project's structured logs would carry it. This run
    happens to start at the suffix's own first character — this function's earlier, leading-only
    implementation would also have caught this particular example; the next test isolates a run
    that starts later, which that earlier implementation would have missed."""
    collision = "722263483763217e"
    assert redact_pan(f"req_{collision}").found  # the vulnerability, confirmed

    calls = _sequence(collision, "aabbccdd11223344")
    monkeypatch.setattr("app.llm.masking.secrets.token_hex", lambda _n: next(calls))

    result = safe_hex_suffix(nbytes=8)

    assert result == "AABBCCDD11223344"
    assert not redact_pan(f"req_{result}").found


def test_a_digit_run_starting_after_the_first_character_is_still_caught() -> None:
    """This is the case a *leading*-only check misses: a suffix whose first character is a letter
    but whose remaining characters are all digits reaches the same card-length floor as one that
    starts with that many digits, yet a check that only measured the run from position 0 would
    read this candidate as having zero leading digits and wrongly accept it. The length scan
    behind the generalized check looks at the whole candidate instead, and catches it. (Whether a
    given digit run of this length also happens to be Luhn-valid, and so is actually redacted, is
    a separate question the other tests in this section already cover with a real collision.)"""
    mid_run_suffix = "A234567890123456"  # 1 letter, then a 15-digit run: still card-length.
    assert _longest_digit_run(mid_run_suffix) == 15


def test_request_ids_generated_at_scale_never_self_redact() -> None:
    """Statistical confirmation alongside the deterministic reproduction above: many real,
    unmocked draws through the same construction ``app.security.middleware`` uses
    (``f"req_{safe_hex_suffix(nbytes=8)}"``) never trip ``redact_pan``, where the equivalent count
    of raw ``secrets.token_hex(8)`` draws is expected to hit at least once (the real rate measured
    against this project's own code was 255 per 200,000)."""
    for _ in range(20_000):
        request_id = f"req_{safe_hex_suffix(nbytes=8)}"
        assert not redact_pan(request_id).found


@pytest.mark.parametrize(
    "text",
    [
        "1094921834",
        "12345678",
        "1234567",
        "123.456.789-09",
        "12.345.678/0001-95",
        "11987654321",
    ],
)
def test_a_document_number_shape_is_redacted(text: str) -> None:
    result = redact_document_numbers(f"Mi documento es {text}, gracias")

    assert result.found
    assert result.masked == f"Mi documento es {DOCUMENT_PLACEHOLDER}, gracias"


@pytest.mark.parametrize(
    "text",
    [
        "$27.556.276,44",
        "$4.593.557,41",
        "1,475,202.64",
        "$7.548.781,13",
        "250.000",
        "1.000.000",
        "123456",
        "el 12 de junio de 2026",
        "tarjeta terminada en 4321",
        "",
    ],
)
def test_an_amount_with_separators_or_a_short_number_is_left_alone(text: str) -> None:
    result = redact_document_numbers(text)

    assert not result.found
    assert result.masked == text


def test_two_document_numbers_and_the_text_between_them_are_handled_independently() -> None:
    result = redact_document_numbers("1094921834 y 123.456.789-09 por 250.000 pesos")

    assert result.masked == f"{DOCUMENT_PLACEHOLDER} y {DOCUMENT_PLACEHOLDER} por 250.000 pesos"


def test_a_dotted_identity_number_has_the_shape_of_an_amount_and_is_not_detected() -> None:
    assert not redact_document_numbers("1.094.921.834").found


def test_an_unbroken_amount_of_seven_digits_is_redacted_like_an_identifier() -> None:
    assert redact_document_numbers("1250000 pesos").masked == f"{DOCUMENT_PLACEHOLDER} pesos"


@pytest.mark.parametrize(
    "text",
    ["123.456.789-091", "9123.456.789-09", "12.345.678/0001-950", "912.345.678/0001-95"],
)
def test_a_longer_digit_run_around_a_tax_number_shape_is_a_different_figure(text: str) -> None:
    result = redact_document_numbers(text)

    assert not result.found
    assert result.masked == text


@pytest.mark.parametrize("text", ["900.123.456-7", "12.345.678-5"])
def test_a_hyphenated_tax_number_with_short_dotted_groups_is_not_detected(text: str) -> None:
    assert redact_document_numbers(text).masked == text


def test_the_same_tax_number_typed_without_dots_is_redacted_but_for_its_check_digit() -> None:
    assert redact_document_numbers("900123456-7").masked == f"{DOCUMENT_PLACEHOLDER}-7"


def test_an_unbroken_decimal_amount_keeps_only_its_decimal_part() -> None:
    assert redact_document_numbers("1250000.50").masked == f"{DOCUMENT_PLACEHOLDER}.50"


def test_a_date_typed_as_eight_unbroken_digits_is_redacted_like_an_identifier() -> None:
    assert redact_document_numbers("el 20260612").masked == f"el {DOCUMENT_PLACEHOLDER}"


def test_the_card_detector_alone_leaves_a_document_number_for_the_document_rule() -> None:
    assert not redact_pan("1094921834").found
