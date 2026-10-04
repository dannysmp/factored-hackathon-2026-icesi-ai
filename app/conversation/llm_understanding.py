"""
Model-Backed Understanding
=============================

Overview
--------
An ``Understanding`` implemented through the LLM port (ADR-7): the customer's message, masked,
goes to a forced structured-extraction tool call; the arguments come back validated as an
``NluResult`` or, failing that, as unusable understanding. Nothing downstream of this class can
tell whether ``FakeNlu`` or a real model produced the result it reads — that is the whole purpose
of the ``Understanding`` port.

Scope
-----
In: building the request (prompt, masked text, forced tool schema), and turning the tool's
arguments into a validated ``NluResult`` with one bounded repair before falling back.
Out: the model call itself (``app.llm.client.LlmClient`` and its Anthropic adapter), masking
(``app.llm.masking``), the prompt file (``app.llm.prompts``), language stickiness across turns and
the deterministic missing-slot guard (``app.conversation.language``, ``app.conversation.guard`` —
both pure functions the controller applies to whatever this class returns).

Design Principles
-----------------
- Structured output, validated: the model returns arguments through a forced tool call, never free
  text; ``_ModelExtraction`` accepts them loosely (no length or cross-field rules), then the
  mapping into ``NluResult`` is where the contract's own bounds and rules apply. A result that
  fails them once is repaired once (truncating an overlong free-text field, reading an empty or
  "null" optional field as absent, capitalising a currency code and dropping one that is not a
  code, dropping a choice out of range, nulling an enum-like value the model spelled wrong or an
  amount that is not a plain figure, dropping a slot reported under an intent that does not read
  it, and reading an intent reported without its slot as ``unclear``) and validated again; the
  transaction the customer described survives each of these. A result that still fails becomes
  ``NluResult.unusable()``: one question, then a person — the customer is never shown a model or
  provider error — and the fields and rules that failed (never the customer's words) are logged.
  A call the port could not complete at all is a different outcome (``UnderstandingUnavailable``,
  raised rather than swallowed): unlike a malformed result, it is not the customer's own
  ambiguity, so it must not be treated as one.
- The masking serializer is the only path text takes to leave the process: this class never builds
  the user message from anything but ``redact_pan(text).masked``.
- Temperature 0: this is structured extraction, not open-ended writing.

Runtime Contract
----------------
``LlmNlu(llm, *, model, prompt=None)`` implementing
``app.conversation.understanding.Understanding``: ``understand(...)`` returns the parsed
``NluResult`` paired with a ``TurnAccounting`` built from the completion's own token/latency
accounting, ``(NluResult.unusable(), None)`` when the call completed but its output was not
usable, or raises ``UnderstandingUnavailable`` when the call could not be completed at all.

Limitations
-----------
The model reports only the customer's own words for a stated transaction date
(``date_expression``); resolving them against ``reference_date`` (AC-E5-16) is a deterministic
step (``app.conversation.date_expressions``), never the model's own arithmetic. Its curated
vocabulary is deliberately narrow (relative day terms, weekday names, a day-of-month phrase, and a
numeric day-first date) — a vague range such as "last week" resolves to nothing, the same as an
expression it never recognized, rather than guessing one specific day out of it.
"""

from __future__ import annotations

# Standard libraries
import logging  # The reasons a result was discarded, field names only
import re  # Matching an amount's currency, digits and separators
import unicodedata  # Control characters are read as spaces, not as a reason to discard a message
from collections.abc import Mapping  # Type of the raw tool arguments
from datetime import date  # The domain calendar's own reference date
from decimal import Decimal, InvalidOperation  # Money is never a float; malformed amounts repair
from typing import cast  # Narrowing a checked-membership str to the closed Lang literal

# Third-party libraries
from pydantic import (  # Loose intermediate model
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
)

# Local modules
from app.conversation.date_expressions import resolve as resolve_date  # AC-E5-16, deterministic
from app.conversation.understanding import (  # What this call cost; raised, never swallowed
    TurnAccounting,
    UnderstandingUnavailable,
)
from app.domain.policy.models import DisputeCategory  # Closed set of dispute categories
from app.llm.client import (  # The port
    CompletionRequest,
    LlmClient,
    LlmError,
    LlmUnavailable,
    ToolSpec,
)
from app.llm.masking import redact_pan  # The only egress path for the customer's own text
from app.llm.prompts import PromptTemplate, load_prompt  # Versioned prompt loading and filling
from contracts.service_v1.envelope import LANGUAGES, Lang  # Closed set of languages
from contracts.service_v1.nlu import (  # The typed result and its vocabulary
    ConfirmationAnswer,
    NluIntent,
    NluResult,
    TransactionHint,
)

logger = logging.getLogger(__name__)

_PROMPT_NAME = "nlu_v1"

_NLU_TOOL = ToolSpec(
    name="record_understanding",
    description=(
        "Record the structured understanding of the customer's newest message: nothing more, "
        "nothing invented."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": [intent.value for intent in NluIntent]},
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "How sure this reading is, 0 to 1.",
            },
            "language": {
                "type": ["string", "null"],
                "enum": [*LANGUAGES, None],
                "description": "The language this message is written in, or null if unclear.",
            },
            "merchant": {"type": ["string", "null"], "maxLength": 80},
            "date_expression": {
                "type": ["string", "null"],
                "maxLength": 40,
                "description": (
                    "The customer's own words for when the transaction happened, verbatim or "
                    "lightly normalized (e.g. 'ayer', 'el lunes', 'the 3rd', '03/04'), or null "
                    "if not stated. Never a resolved date: report the words only."
                ),
            },
            "amount": {
                "type": ["string", "null"],
                "description": (
                    "The amount as plain decimal text: digits with a point before the cents, "
                    'no thousands separator, e.g. "99948.89" for "99.948,89", or null.'
                ),
            },
            "currency": {
                "type": ["string", "null"],
                "description": 'The ISO 4217 code, e.g. "MXN", or null.',
            },
            "product_last4": {
                "type": ["string", "null"],
                "description": "Last four digits of the product mentioned, or null.",
            },
            "category": {
                "type": ["string", "null"],
                "enum": [category.value for category in DisputeCategory] + [None],
            },
            "detail": {"type": ["string", "null"], "maxLength": 500},
            "confirmation": {
                "type": ["string", "null"],
                "enum": [answer.value for answer in ConfirmationAnswer] + [None],
            },
            "choice": {"type": ["integer", "null"], "minimum": 1, "maximum": 5},
            "requested_language": {"type": ["string", "null"], "enum": [*LANGUAGES, None]},
            "policy_query": {"type": ["string", "null"], "maxLength": 200},
            "mentions_second_dispute": {"type": "boolean"},
        },
        "required": ["intent", "confidence", "mentions_second_dispute"],
    },
)

# NluResult.choice's own bound (contracts/service_v1/nlu.py); repeated here so a choice out of
# range can be nulled without constructing the contract model just to find that out.
_MIN_CHOICE = 1
_MAX_CHOICE = 5

# Free-text fields worth one truncation attempt when the model overruns the contract's bound.
_LENGTH_REPAIRS: tuple[tuple[str, int], ...] = (
    ("merchant", 80),
    ("date_expression", 40),
    ("detail", 500),
    ("policy_query", 200),
)

# Optional fields a model fills with an empty string or the word "null" when the message says
# nothing about them; read as absent, never as a value that voids the whole understanding.
_OPTIONAL_FIELDS = (
    "language",
    "merchant",
    "date_expression",
    "amount",
    "currency",
    "product_last4",
    "category",
    "detail",
    "confirmation",
    "requested_language",
    "policy_query",
)
_ABSENT_WORDS = frozenset({"", "null", "none"})
_CURRENCY_FORM = re.compile(r"^[A-Z]{3}$")
_LAST4_FORM = re.compile(r"^\d{4}$")

# The one intent each slot exists for (``NluResult`` refuses the slot under any other intent, and
# the intent without its slot).
_SLOT_OWNER: Mapping[str, str] = {
    "confirmation": NluIntent.CONFIRMATION.value,
    "choice": NluIntent.CHOICE.value,
    "requested_language": NluIntent.SWITCH_LANGUAGE.value,
}

# Enum-like fields worth nulling out, rather than discarding the whole result, when the model
# spelled a value that is not in the closed set (for example a language code it invented).
_ENUM_REPAIRS: Mapping[str, frozenset[str]] = {
    "language": frozenset(LANGUAGES),
    "requested_language": frozenset(LANGUAGES),
    "category": frozenset(category.value for category in DisputeCategory),
    "confirmation": frozenset(answer.value for answer in ConfirmationAnswer),
}


# A figure is read only when it is exactly: an optional currency (a symbol, or one of the ISO 4217
# codes the bank's customers write, in either case, or "U$S" as Rioplatense Spanish writes dollars),
# a number whose marks are digit groups and separators, and an optional currency. Anything else (a
# sign, a percentage, an exponent, words, a code outside this list, digit runs split by text) is
# not an amount and is never repaired into one.
_CURRENCY_CODES = (
    "ARS|BOB|BRL|CLP|COP|CRC|CUP|DOP|EUR|GBP|GTQ|HNL|JPY|MXN|NIO|PAB|PEN|PYG|USD|UYU|VES"
)
_CURRENCY = rf"(?:(?i:(?:{_CURRENCY_CODES})\$?|U\$S|US\$)|R\$|\$|€|£|¥)"
_SPACING = " \u00a0\u202f"
_AMOUNT_TEXT = re.compile(
    rf"(?:{_CURRENCY}[{_SPACING}]*)?"
    rf"(?P<figure>[.,]?[0-9](?:[0-9.,'\u2019{_SPACING}]*[0-9])?)"
    rf"(?:[{_SPACING}]*{_CURRENCY})?"
)
# Spacing and apostrophes group digits the way "." or "," does; all are one mark while parsing.
_GROUPING_MARKS = re.compile(rf"['\u2019{_SPACING}]")
# Money groups thousands in threes, and two decimal places mean "1.234" is 1234, not 1.234.
_THOUSANDS_GROUP = 3


def _parse_amount(text: str) -> Decimal:
    """The amount in ``text``, however the customer's locale writes its separators.

    A model told to give plain decimal text still sometimes copies the customer's own figure
    ("99.948,89", "99,948.89", "$ 1 250,50", "ARS 99948.89"). A currency symbol or code at either
    end is dropped; with both separators present the last is the decimal point; a mark that
    repeats, or stands once before exactly three digits, groups thousands; any other single
    separator is the decimal point. Spacing and apostrophes only ever group thousands.

    Raises
    ------
    decimal.InvalidOperation
        ``text`` is not an amount, or its marks are not any locale's grouping.
    """
    match = _AMOUNT_TEXT.fullmatch(text.strip())
    if match is None:
        raise InvalidOperation(text)
    figure = _GROUPING_MARKS.sub("'", match["figure"])
    separators = [mark for mark in (".", ",") if mark in figure]
    if not separators:
        return Decimal(_ungrouped(figure, "'") if "'" in figure else figure)
    decimal_point = max(separators, key=figure.rindex)
    others = [mark for mark in (".", ",", "'") if mark in figure and mark != decimal_point]
    if others:
        grouped, _, fraction = figure.rpartition(decimal_point)
        return Decimal(f"{_ungrouped(grouped, others[0])}.{fraction}")
    integer, *later = figure.split(decimal_point)
    repeated = len(later) > 1
    stands_before_a_group = len(later[0]) == _THOUSANDS_GROUP and integer.strip("0") != ""
    if repeated or stands_before_a_group:
        return Decimal(_ungrouped(figure, decimal_point))
    return Decimal(f"{integer or '0'}.{later[0]}")


def _ungrouped(digits: str, separator: str) -> str:
    """``digits`` without its thousands ``separator``; raises when the groups are not thousands."""
    head, *groups = digits.split(separator)
    if not 1 <= len(head) <= _THOUSANDS_GROUP or any(
        len(group) != _THOUSANDS_GROUP for group in groups
    ):
        raise InvalidOperation(digits)
    return head + "".join(groups)


def _amount_text(value: object) -> object:
    """A JSON number the model sent as an amount, as text; any other value as it came."""
    if isinstance(value, int | float):
        return str(value)
    return value


def _is_amount(value: object) -> bool:
    """Whether ``value`` reads as an amount the contract's own ``TransactionHint`` accepts."""
    text = _amount_text(value)
    if not isinstance(text, str):
        return False
    try:
        TransactionHint(amount=_parse_amount(text))
    except (InvalidOperation, ValidationError):
        return False
    return True


class _ModelExtraction(BaseModel):
    """The tool call's arguments, accepted loosely; the contract's own rules apply on mapping."""

    model_config = ConfigDict(extra="ignore")

    intent: NluIntent
    confidence: float
    language: str | None = None
    merchant: str | None = None
    date_expression: str | None = None
    amount: str | None = None
    currency: str | None = None
    product_last4: str | None = None
    category: str | None = None
    detail: str | None = None
    confirmation: str | None = None
    choice: int | None = None
    requested_language: str | None = None
    policy_query: str | None = None
    mentions_second_dispute: bool = False

    @field_validator("amount", mode="before")
    @classmethod
    def _number_as_text(cls, value: object) -> object:
        """Read an amount sent as a JSON number the same way as one sent as text."""
        return _amount_text(value)


def _to_nlu_result(extraction: _ModelExtraction, *, reference_date: date) -> NluResult:
    """Map a validated extraction into the contract's own, stricter shape.

    ``reference_date`` resolves ``extraction.date_expression`` (AC-E5-16), never the model's own
    arithmetic; an expression the closed vocabulary does not recognize resolves to nothing, the
    same as no date stated at all.

    Raises
    ------
    pydantic.ValidationError
        A contract-level rule is not satisfied (a bound exceeded, a slot read for the wrong
        intent, and so on).
    decimal.InvalidOperation
        ``amount`` does not parse as decimal text.
    """
    language = cast(Lang, extraction.language) if extraction.language in LANGUAGES else None
    resolved_date = (
        resolve_date(extraction.date_expression, language=language, reference_date=reference_date)
        if extraction.date_expression
        else None
    )
    transaction = TransactionHint(
        merchant=extraction.merchant,
        amount=_parse_amount(extraction.amount) if extraction.amount else None,
        currency=extraction.currency,
        date_on=resolved_date[0] if resolved_date is not None else None,
        date_source=resolved_date[1] if resolved_date is not None else None,
        product_last4=extraction.product_last4,
    )
    return NluResult(
        intent=extraction.intent,
        confidence=extraction.confidence,
        language=extraction.language,
        transaction=transaction,
        category=extraction.category,
        detail=extraction.detail,
        confirmation=extraction.confirmation,
        choice=extraction.choice,
        requested_language=extraction.requested_language,
        policy_query=extraction.policy_query,
        mentions_second_dispute=extraction.mentions_second_dispute,
    )


def _cleaned_optional_fields(repaired: dict[str, object]) -> None:
    """Read an empty, blank or "null" optional field as absent, and tidy the codes."""
    for key in _OPTIONAL_FIELDS:
        value = repaired.get(key)
        if isinstance(value, str):
            cleaned = "".join(" " if unicodedata.category(c) == "Cc" else c for c in value).strip()
            repaired[key] = None if cleaned.lower() in _ABSENT_WORDS else cleaned
    currency = repaired.get("currency")
    if isinstance(currency, str):
        repaired["currency"] = currency.upper() if _CURRENCY_FORM.match(currency.upper()) else None
    last4 = repaired.get("product_last4")
    if isinstance(last4, str) and not _LAST4_FORM.match(last4):
        repaired["product_last4"] = None


def _slots_matched_to_intent(repaired: dict[str, object]) -> None:
    """Drop a slot reported under an intent that does not read it; read an intent reported without
    its slot as ``unclear``."""
    intent = repaired.get("intent")
    for slot, owner in _SLOT_OWNER.items():
        if repaired.get(slot) is not None and intent != owner:
            repaired[slot] = None
    for slot, owner in _SLOT_OWNER.items():
        if repaired.get(slot) is None and intent == owner:
            repaired["intent"] = NluIntent.UNCLEAR.value


def _repaired(raw: Mapping[str, object]) -> dict[str, object]:
    """One bounded repair of the raw tool arguments: truncate, clamp, or null — never re-ask.

    A slot reported under an intent that does not read it is dropped, and an intent reported
    without the slot it needs is read as ``unclear`` — in both cases the rest of the message's
    understanding (the transaction it describes, above all) is kept rather than discarded with
    it. A violation no field can be dropped to resolve falls back to unusable understanding.
    """
    repaired = dict(raw)
    _cleaned_optional_fields(repaired)
    for key, limit in _LENGTH_REPAIRS:
        value = repaired.get(key)
        if isinstance(value, str) and len(value) > limit:
            repaired[key] = value[:limit]
    for key, allowed in _ENUM_REPAIRS.items():
        value = repaired.get(key)
        if isinstance(value, str) and value not in allowed:
            repaired[key] = None
    if repaired.get("amount") is not None and not _is_amount(repaired["amount"]):
        repaired["amount"] = None
    choice = repaired.get("choice")
    if isinstance(choice, int) and not (_MIN_CHOICE <= choice <= _MAX_CHOICE):
        repaired["choice"] = None
    _slots_matched_to_intent(repaired)
    return repaired


# Failures a single bounded repair is worth attempting for: the model's JSON did not match the
# loose intermediate model, the strict contract's own rules, or the amount did not parse.
_REPAIRABLE_ERRORS = (ValidationError, InvalidOperation, TypeError, ValueError)


def _discard_causes(error: Exception) -> str:
    """Which fields, and which kinds of rule, made a result unusable — never the values, which
    carry the customer's own words."""
    if isinstance(error, ValidationError):
        return ",".join(
            sorted(
                {
                    f"{'.'.join(str(part) for part in e['loc']) or 'result'}:{e['type']}"
                    for e in error.errors()
                }
            )
        )
    return type(error).__name__


def _parse(tool_input: Mapping[str, object], *, reference_date: date) -> NluResult:
    """Validate the tool's arguments into an ``NluResult``, with one bounded repair attempt."""
    try:
        return _to_nlu_result(
            _ModelExtraction.model_validate(tool_input), reference_date=reference_date
        )
    except _REPAIRABLE_ERRORS:
        pass
    try:
        return _to_nlu_result(
            _ModelExtraction.model_validate(_repaired(tool_input)), reference_date=reference_date
        )
    except _REPAIRABLE_ERRORS as error:
        logger.warning("understanding_discarded causes=%s", _discard_causes(error))
        return NluResult.unusable()


class LlmNlu:
    """``Understanding`` implemented through the LLM port."""

    def __init__(self, llm: LlmClient, *, model: str, prompt: PromptTemplate | None = None) -> None:
        """
        Parameters
        ----------
        llm : LlmClient
            The port to call; a real adapter or ``FakeLlm`` in tests.
        model : str
            A model id from the configuration allow-list (the composition root's job to check).
        prompt : PromptTemplate | None
            The prompt to use; loaded from ``prompts/nlu_v1.yaml`` when omitted.
        """
        self._llm = llm
        self._model = model
        self._prompt = prompt or load_prompt(_PROMPT_NAME)

    def understand(
        self, text: str, *, language_hint: Lang | None, reference_date: date
    ) -> tuple[NluResult, TurnAccounting | None]:
        """Understand ``text`` through the model, or return unusable understanding.

        ``reference_date`` resolves a customer-stated transaction date (AC-E5-16) against the
        domain calendar's own reference date — never the wall clock.

        Empty text and an invalid call's output are both treated as unusable: the customer is
        never shown a model or provider error, only asked again — genuine ambiguity a
        clarification question can resolve, and neither produces accounting, since no real,
        priced call completed. A call that could not reach the provider at all, after its own
        bounded retries, is a different outcome and raises ``UnderstandingUnavailable`` instead:
        that is not the customer's ambiguity to clarify, and it produces no accounting either.

        Raises
        ------
        UnderstandingUnavailable
            The underlying call raised ``LlmUnavailable`` — the provider or the circuit breaker
            in front of it could not be reached, even after retrying.
        """
        if not text.strip():
            return NluResult.unusable(), None

        masked = redact_pan(text).masked
        user_text = self._prompt.render_task(
            language_hint=language_hint or "unknown", message=masked
        )
        request = CompletionRequest(
            model=self._model,
            system=self._prompt.system,
            user_text=user_text,
            tool=_NLU_TOOL,
            prompt_version=self._prompt.version,
            temperature=0.0,
        )
        try:
            result = self._llm.complete(request)
        except LlmUnavailable as error:
            raise UnderstandingUnavailable(str(error)) from error
        except LlmError:
            return NluResult.unusable(), None
        accounting = TurnAccounting(
            model=result.model,
            prompt_version=result.prompt_version,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
        )
        return _parse(result.tool_input, reference_date=reference_date), accounting
