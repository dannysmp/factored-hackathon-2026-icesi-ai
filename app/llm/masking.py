"""
Masking Serializer
===================

Overview
--------
The egress control every request applies to a customer's free text immediately before it leaves
the process for the language model, the boundary where PII is minimized. Card-shaped (PAN-like)
digit runs and document-number-shaped values are found and replaced with fixed placeholders;
nothing else in the text is touched.

Scope
-----
In: the card digit-run detector and the redaction it applies, the document-number detector and
its redaction, and ``safe_hex_suffix``, generating a
reference-number suffix guaranteed never to combine with the digits before it into something this
detector would itself flag — the one other place in this project that needs to reason about the
same digit-run rule, not a second implementation of it.
Out: what a caller does with masked text (the LLM port), and masking of any other field kind (none
exists yet: the only outbound free text is the customer's message to the NLU adapter; a future
caller that sends another masked field, for example a name or a contact detail, extends this module
rather than duplicating the pattern elsewhere).

Design Principles
-----------------
- This check lives here rather than in the contract layer because a contract field can only accept
  or refuse a whole value; it cannot redact a card number sitting inside an otherwise legitimate
  sentence. The masking serializer can, and it is the one place every outbound request passes
  through, so the check cannot be bypassed by a path that skips a particular contract.
- A digit run is card-shaped by two tests together, not one: its length falls in the range real
  PANs use (13 to 19 digits, inclusive, covering every major network) and it passes the Luhn
  checksum every issued card number satisfies. Length alone flags too much (a 16-digit order
  number, a long phone number with a country code); the checksum alone is not tried at every
  length. Both together is what keeps the false-positive set (order numbers, phone numbers,
  reference codes of card-like length) clean while still catching a real card number typed with
  or without separators.
- Detection reads a maximal run of digits and single separator characters (space, hyphen, comma,
  underscore, period — the shapes people actually type a card number with, plus no separator at
  all) as one blob, rather than matching the whole blob as one unit. Every digit in the blob that
  belongs to at least one Luhn-valid, PAN-length window starting anywhere in the blob is marked for
  redaction, and marked digits that sit next to each other merge into one redacted span. This is
  what keeps two full card numbers typed back to back, with nothing but a single separator between
  them, both fully redacted even when a window straddling the boundary between the two also happens
  to pass the checksum: taking only the first or only the longest valid window at each position can
  leave part of a real card number outside the one window chosen; marking every digit that any
  valid window covers cannot.
- Redaction replaces only the matched digits and their internal separators; surrounding text,
  including any other digits in the same field, is untouched.
- A document number has no checksum, so it is found by shape instead, and the shapes are chosen so
  that a money amount survives: an unbroken run of seven or more digits (a national identity
  number typed plainly, or a long phone number), and the two punctuated Brazilian shapes, a
  personal tax number (``123.456.789-09``) and a company tax number (``12.345.678/0001-95``). A
  figure written with thousands separators and a decimal part (``27.556.276,44``,
  ``1,475,202.64``) is never an unbroken run that long, so it passes through untouched and the
  amount the customer states still reaches the model.
- The card detector alone serves the log path (``app.observability.logging``): the document-number
  rule is for the customer's own words going to the model, where a long unbroken run is far more
  likely an identifier than anything else; in a log line one is as likely a timestamp or an id.

Runtime Contract
----------------
``redact_pan(text) -> Redaction`` with ``masked`` (the text to send) and ``found`` (whether
anything was redacted, for the request-capture fixture and for accounting).
``redact_document_numbers(text) -> Redaction``, the same result shape for document-number
shapes, applied to text already passed through ``redact_pan``. The order matters: a card number
is a long run of digits, and a caller that looked for document numbers first would redact it
under the wrong placeholder and report no card.
``safe_hex_suffix(nbytes=4, *, preceding_digits=0) -> str``, an uppercase hex string of
``2 * nbytes`` characters.

Limitations
-----------
The detector runs on plain text and cannot see a card number split across two separate messages,
or one written entirely in words. Its adversarial robustness and false-positive rate are measured
against a fixed adversarial set (separator variants, adjacent non-digit characters, multiple
card-like runs in one field) and a fixed false-positive set (order numbers, phone numbers,
reference codes of card-like length), not proven exhaustively.

The document-number rule has two known gaps, both chosen over the alternative of redacting
amounts. An amount typed as seven or more unbroken digits (``1250000``) is redacted like an
identifier; it is only a search hint for the customer's own transactions, and the stored amount is
what policy reads, so the cost is one more question to the customer. A national identity number
typed with thousands-style dots (``1.094.921.834``) has the shape of an amount and passes through
unmasked. Identity numbers split by anything else, or written in words, are not detected.

Three further consequences follow from detecting by shape. A tax number written with a hyphen and
shorter dotted groups (a Colombian ``900.123.456-7``, a Chilean ``12.345.678-5``) has no shape the
rule recognizes and passes through; the same number typed without dots is an unbroken run and is
redacted, leaving only its hyphenated check digit. An unbroken decimal amount (``1250000.50``) has
its whole part redacted and the decimal part left. A date typed as eight unbroken digits
(``20260612``) is redacted like an identifier, so it is not available as a search hint.
"""

from __future__ import annotations

# Standard libraries
import re  # Locating maximal digit-and-separator runs
import secrets  # Generating a reference suffix that cannot look card-shaped
from dataclasses import dataclass  # Immutable result

PLACEHOLDER = "[card-number-redacted]"
DOCUMENT_PLACEHOLDER = "[document-number-redacted]"

# A card number as people actually type it: digits, optionally separated by one of these
# characters between any two digits, never two separators in a row.
_SEPARATORS = " -,_."
_RUN = re.compile(rf"\d(?:[{re.escape(_SEPARATORS)}]?\d)*")

# Real PANs run 13 to 19 digits across every major network (Visa, Mastercard, Amex, and the
# longer ranges some debit and prepaid products use).
_MIN_PAN_DIGITS = 13
_MAX_PAN_DIGITS = 19

# A personal tax number, a company tax number, or any unbroken run of seven or more digits. Each
# is matched only as a whole figure: a longer run of digits that happens to contain one of the
# punctuated shapes is a different figure, not that tax number, and passes through.
_DOCUMENT_NUMBER = re.compile(
    r"(?<!\d)(?:\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{7,})(?!\d)"
)


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

    Inside each maximal digit-and-separator run, every start position is checked for a Luhn-valid
    window of every length in range (not only the position right after the last window taken), so
    a second real card number's own window is never missed because an earlier scan step already
    consumed part of its digits. Each valid window becomes one text interval (from its first digit
    to its last, carrying any separators between them); overlapping or touching intervals are then
    merged into one span. Two card numbers with an ordinary separator between them stay two spans,
    since their windows' text intervals do not touch; a window that happens to straddle the
    boundary between two adjacent card numbers and is itself Luhn-valid pulls both into one merged
    span, which is what keeps that boundary from leaving either number only partly redacted.
    """
    windows: list[tuple[int, int]] = []
    for match in _RUN.finditer(text):
        digits_only = "".join(char for char in match.group(0) if char.isdigit())
        positions = _digit_positions(match.group(0), match.start())
        for start_index in range(len(positions)):
            remaining = len(positions) - start_index
            for length in range(_MIN_PAN_DIGITS, min(_MAX_PAN_DIGITS, remaining) + 1):
                if _luhn_valid(digits_only[start_index : start_index + length]):
                    windows.append(
                        (positions[start_index].start, positions[start_index + length - 1].end)
                    )
    if not windows:
        return []
    windows.sort()
    spans: list[tuple[int, int]] = [windows[0]]
    for start, end in windows[1:]:
        last_start, last_end = spans[-1]
        if start <= last_end:
            spans[-1] = (last_start, max(last_end, end))
        else:
            spans.append((start, end))
    return spans


@dataclass(frozen=True, slots=True)
class Redaction:
    """The result of redacting one text: the text to send and whether anything was redacted."""

    masked: str
    found: bool


def redact_pan(text: str) -> Redaction:
    """Replace every card-shaped digit run in ``text`` with :data:`PLACEHOLDER`.

    Parameters
    ----------
    text : str
        Free text about to leave the process for a language model.

    Returns
    -------
    Redaction
        ``masked`` is safe to send; ``found`` is ``True`` when at least one run was redacted, for
        the caller's own accounting (never for a customer-visible message: what triggered
        redaction is not disclosed, the same rule the rest of the service applies to a routing
        reason).
    """
    spans = _card_shaped_spans(text)
    if not spans:
        return Redaction(masked=text, found=False)
    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        pieces.append(text[cursor:start])
        pieces.append(PLACEHOLDER)
        cursor = end
    pieces.append(text[cursor:])
    return Redaction(masked="".join(pieces), found=True)


def redact_document_numbers(text: str) -> Redaction:
    """Replace every document-number-shaped value in ``text`` with :data:`DOCUMENT_PLACEHOLDER`.

    Parameters
    ----------
    text : str
        Free text about to leave the process for a language model, already passed through
        :func:`redact_pan`.

    Returns
    -------
    Redaction
        ``masked`` is safe to send; ``found`` is ``True`` when at least one value was redacted.
        Detection is by shape: hyphenated tax numbers with short dotted groups are not detected,
        an unbroken decimal amount keeps its decimal part, and an unbroken eight-digit date is
        redacted.
    """
    masked, count = _DOCUMENT_NUMBER.subn(DOCUMENT_PLACEHOLDER, text)
    return Redaction(masked=masked, found=count > 0)


def _longest_digit_run(text: str) -> int:
    """The length of the longest maximal run of decimal digits anywhere in ``text``."""
    return max((len(run) for run in re.findall(r"\d+", text)), default=0)


def safe_hex_suffix(nbytes: int = 4, *, preceding_digits: int = 0) -> str:
    """An uppercase random hex string with no digit run — on its own, or combined with
    ``preceding_digits`` digits immediately before it — long enough to be card-shaped.

    Two things can go wrong with a random hex string used in a customer-facing identifier, and
    this guards against both:

    - Joined right after a run of digits with no separator :func:`redact_pan`'s own detector
      treats as breaking a run (a hyphen, for instance — the join this project's
      ``T-YYYYMMDD-XXXXXXXX`` handoff tickets and ``CASE-YYYYMMDD-XXXXXXXX`` case numbers use), a
      suffix whose leading characters happen to all be digits (no ``A``-``F``) extends that run.
    - Entirely on its own, once a suffix is long enough (``request_id``'s 16-character suffix, not
      joined to anything), a run of digits can reach the floor without ever touching either edge.

    Both failures mean the same thing: some maximal digit run — considering ``preceding_digits``
    real digits glued to this suffix's own start — reaches this module's own ``_MIN_PAN_DIGITS``
    floor, and is then occasionally Luhn-valid purely by chance, so a plain identifier gets treated
    as a leaked card number. This is regenerated until no such run exists, rather than trusting a
    probability this project has already seen fail in practice twice.
    """
    while True:
        candidate = secrets.token_hex(nbytes).upper()
        probe = ("0" * preceding_digits) + candidate
        if _longest_digit_run(probe) < _MIN_PAN_DIGITS:
            return candidate
