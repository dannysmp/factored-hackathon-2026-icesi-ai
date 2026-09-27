"""
Masking Serializer
===================

Overview
--------
The egress control every request applies to a customer's free text immediately before it leaves
the process for the language model, the boundary the architecture names for PII minimization
(``plan/docs/architecture.md``, Security and privacy). Card-shaped (PAN-like) digit runs are found
and replaced with a fixed placeholder; nothing else in the text is touched.

Scope
-----
In: the digit-run detector and the redaction it applies.
Out: what a caller does with masked text (the LLM port), and masking of any other field kind
(none exists yet: this slice's only outbound free text is the customer's message to the NLU
adapter; a future caller that sends another masked field, for example a name or a contact detail,
extends this module rather than duplicating the pattern elsewhere).

Design Principles
-----------------
- This check moved here from the contract layer (thot-follow-up issue #80) because a contract
  field can only accept or refuse a whole value; it cannot redact a card number sitting inside an
  otherwise legitimate sentence. The masking serializer can, and it is the one place every
  outbound request passes through, so the check cannot be bypassed by a path that skips a
  particular contract.
- A digit run is card-shaped by two tests together, not one: its length falls in the range real
  PANs use (13 to 19 digits, inclusive, covering every major network) and it passes the Luhn
  checksum every issued card number satisfies. Length alone flags too much (a 16-digit order
  number, a long phone number with a country code); the checksum alone is not tried at every
  length. Both together is what keeps the false-positive set (order numbers, phone numbers,
  reference codes of card-like length) clean while still catching a real card number typed with
  or without separators.
- Detection reads a maximal run of digits and single separator characters (space, hyphen, comma,
  underscore, period — the shapes people actually type a card number with, plus no separator at
  all) as one blob, then slides a window inside it rather than matching the whole blob as one
  unit. This is what keeps two card-shaped runs typed back to back, with nothing but a single
  space between them, each redacted on their own instead of the pair being read as one
  wrong-length blob that the length test then waves through unredacted.
- Redaction replaces only the matched digits and their internal separators; surrounding text,
  including any other digits in the same field, is untouched.

Runtime Contract
----------------
``redact_pan(text) -> PanRedaction`` with ``masked`` (the text to send) and ``found`` (whether
anything was redacted, for the request-capture fixture and for accounting).

Limitations
-----------
The detector runs on plain text and cannot see a card number split across two separate messages,
or one written entirely in words. Its adversarial robustness and false-positive rate are measured
against the sets thot-follow-up issue #80 names, not proven exhaustively.
"""

from __future__ import annotations

# Standard libraries
import re  # Locating maximal digit-and-separator runs
from dataclasses import dataclass  # Immutable result

PLACEHOLDER = "[card-number-redacted]"

# A card number as people actually type it: digits, optionally separated by one of these
# characters between any two digits, never two separators in a row.
_SEPARATORS = " -,_."
_RUN = re.compile(rf"\d(?:[{re.escape(_SEPARATORS)}]?\d)*")

# Real PANs run 13 to 19 digits across every major network (Visa, Mastercard, Amex, and the
# longer ranges some debit and prepaid products use).
_MIN_PAN_DIGITS = 13
_MAX_PAN_DIGITS = 19


_SINGLE_DIGIT_MAX = 9  # A doubled digit above this needs its own digits summed (Luhn's rule).


def _luhn_valid(digits: str) -> bool:
    """Whether ``digits`` (a string of decimal digits) passes the Luhn checksum."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > _SINGLE_DIGIT_MAX:
                value -= _SINGLE_DIGIT_MAX
        total += value
    return total % 10 == 0


@dataclass(frozen=True, slots=True)
class _DigitPosition:
    """One digit of a run: its index in the run's digit-only string and its span in the text."""

    digit_index: int
    start: int
    end: int


def _digit_positions(run: str, run_start: int) -> list[_DigitPosition]:
    """Every digit of ``run`` (a match of ``_RUN``) with its span in the original text."""
    positions: list[_DigitPosition] = []
    digit_index = 0
    for offset, char in enumerate(run):
        if char.isdigit():
            absolute = run_start + offset
            positions.append(_DigitPosition(digit_index, absolute, absolute + 1))
            digit_index += 1
    return positions


def _card_shaped_spans(text: str) -> list[tuple[int, int]]:
    """Spans in ``text`` that are card-shaped: length in range and Luhn-valid.

    Scans left to right inside each maximal digit-and-separator run, preferring the longest
    valid window at each start position, so a full card number is redacted whole rather than a
    shorter Luhn-valid prefix of it; a start with no valid window advances by one digit.
    """
    spans: list[tuple[int, int]] = []
    for match in _RUN.finditer(text):
        digits_only = "".join(char for char in match.group(0) if char.isdigit())
        positions = _digit_positions(match.group(0), match.start())
        start_index = 0
        while start_index < len(positions):
            remaining = len(positions) - start_index
            found_at_start = False
            for length in range(min(_MAX_PAN_DIGITS, remaining), _MIN_PAN_DIGITS - 1, -1):
                candidate = digits_only[start_index : start_index + length]
                if _luhn_valid(candidate):
                    span_start = positions[start_index].start
                    span_end = positions[start_index + length - 1].end
                    spans.append((span_start, span_end))
                    start_index += length
                    found_at_start = True
                    break
            if not found_at_start:
                start_index += 1
    return spans


@dataclass(frozen=True, slots=True)
class PanRedaction:
    """The result of scanning one piece of text for card-shaped digit runs."""

    masked: str
    found: bool


def redact_pan(text: str) -> PanRedaction:
    """Replace every card-shaped digit run in ``text`` with :data:`PLACEHOLDER`.

    Parameters
    ----------
    text : str
        Free text about to leave the process for a language model.

    Returns
    -------
    PanRedaction
        ``masked`` is safe to send; ``found`` is ``True`` when at least one run was redacted, for
        the caller's own accounting (never for a customer-visible message: what triggered
        redaction is not disclosed, the same rule the rest of the service applies to a routing
        reason).
    """
    spans = _card_shaped_spans(text)
    if not spans:
        return PanRedaction(masked=text, found=False)
    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        pieces.append(text[cursor:start])
        pieces.append(PLACEHOLDER)
        cursor = end
    pieces.append(text[cursor:])
    return PanRedaction(masked="".join(pieces), found=True)
