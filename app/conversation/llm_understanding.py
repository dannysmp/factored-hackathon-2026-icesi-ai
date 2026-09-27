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
  fails them once is repaired once (truncating an overlong free-text field, dropping a choice out
  of range, nulling an enum-like value the model spelled wrong) and validated again; a result that
  still fails, or a call the port itself could not complete, becomes ``NluResult.unusable()``: one
  question, then a person — the customer is never shown a model or provider error.
- The masking serializer is the only path text takes to leave the process: this class never builds
  the user message from anything but ``redact_pan(text).masked``.
- Temperature 0: this is structured extraction, not open-ended writing.

Runtime Contract
----------------
``LlmNlu(llm, *, model, prompt=None)`` implementing
``app.conversation.understanding.Understanding``.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Mapping  # Type of the raw tool arguments
from decimal import Decimal, InvalidOperation  # Money is never a float; malformed amounts repair

# Third-party libraries
from pydantic import BaseModel, ConfigDict, ValidationError  # Loose intermediate model

from app.domain.policy.models import DisputeCategory  # Closed set of dispute categories

# Local modules
from app.llm.client import CompletionRequest, LlmClient, LlmError, ToolSpec  # The port
from app.llm.masking import redact_pan  # The only egress path for the customer's own text
from app.llm.prompts import PromptTemplate, load_prompt  # Versioned prompt loading and filling
from contracts.service_v1.envelope import LANGUAGES, Lang  # Closed set of languages
from contracts.service_v1.nlu import (  # The typed result and its vocabulary
    ConfirmationAnswer,
    NluIntent,
    NluResult,
    TransactionHint,
)

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
            "amount": {
                "type": ["string", "null"],
                "description": 'The amount as decimal text, e.g. "125.50", or null.',
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
    ("detail", 500),
    ("policy_query", 200),
)

# Enum-like fields worth nulling out, rather than discarding the whole result, when the model
# spelled a value that is not in the closed set (for example a language code it invented).
_ENUM_REPAIRS: Mapping[str, frozenset[str]] = {
    "language": frozenset(LANGUAGES),
    "requested_language": frozenset(LANGUAGES),
    "category": frozenset(category.value for category in DisputeCategory),
    "confirmation": frozenset(answer.value for answer in ConfirmationAnswer),
}


class _ModelExtraction(BaseModel):
    """The tool call's arguments, accepted loosely; the contract's own rules apply on mapping."""

    model_config = ConfigDict(extra="ignore")

    intent: NluIntent
    confidence: float
    language: str | None = None
    merchant: str | None = None
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


def _to_nlu_result(extraction: _ModelExtraction) -> NluResult:
    """Map a validated extraction into the contract's own, stricter shape.

    Raises
    ------
    pydantic.ValidationError
        A contract-level rule is not satisfied (a bound exceeded, a slot read for the wrong
        intent, and so on).
    decimal.InvalidOperation
        ``amount`` does not parse as decimal text.
    """
    transaction = TransactionHint(
        merchant=extraction.merchant,
        amount=Decimal(extraction.amount) if extraction.amount else None,
        currency=extraction.currency,
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


def _repaired(raw: Mapping[str, object]) -> dict[str, object]:
    """One bounded repair of the raw tool arguments: truncate, clamp, or null — never re-ask.

    Cross-field rules (a slot belonging to its intent, for example) are not repaired here; a
    violation of one of those means the extraction is genuinely inconsistent, and the caller falls
    back to unusable understanding rather than guessing which side of it was right.
    """
    repaired = dict(raw)
    for key, limit in _LENGTH_REPAIRS:
        value = repaired.get(key)
        if isinstance(value, str) and len(value) > limit:
            repaired[key] = value[:limit]
    for key, allowed in _ENUM_REPAIRS.items():
        value = repaired.get(key)
        if isinstance(value, str) and value not in allowed:
            repaired[key] = None
    choice = repaired.get("choice")
    if isinstance(choice, int) and not (_MIN_CHOICE <= choice <= _MAX_CHOICE):
        repaired["choice"] = None
    return repaired


# Failures a single bounded repair is worth attempting for: the model's JSON did not match the
# loose intermediate model, the strict contract's own rules, or the amount did not parse.
_REPAIRABLE_ERRORS = (ValidationError, InvalidOperation, TypeError, ValueError)


def _parse(tool_input: Mapping[str, object]) -> NluResult:
    """Validate the tool's arguments into an ``NluResult``, with one bounded repair attempt."""
    try:
        return _to_nlu_result(_ModelExtraction.model_validate(tool_input))
    except _REPAIRABLE_ERRORS:
        pass
    try:
        return _to_nlu_result(_ModelExtraction.model_validate(_repaired(tool_input)))
    except _REPAIRABLE_ERRORS:
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

    def understand(self, text: str, *, language_hint: Lang | None) -> NluResult:
        """Understand ``text`` through the model, or return unusable understanding.

        Empty text and a failed or invalid call are both treated as unusable: the customer is
        never shown a model or provider error, only asked again.
        """
        if not text.strip():
            return NluResult.unusable()

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
        except LlmError:
            return NluResult.unusable()
        return _parse(result.tool_input)
