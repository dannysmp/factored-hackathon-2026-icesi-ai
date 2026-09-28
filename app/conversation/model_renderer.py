"""
Model-Backed Rendering
=======================

Overview
--------
Writes one customer reply through the LLM port: the envelope's intent and the closed set of
placeholders it may cite (never a grounded value itself, only the field names —
``app.conversation.verifier`` substitutes the actual values afterward), forced through a tool call,
with the same bounded-repair-then-``None`` discipline ``LlmNlu`` uses for understanding. Nothing
downstream can tell a real model or ``FakeLlm`` produced the candidate.

Scope
-----
In: building the request (prompt, forced tool schema) and turning the tool's arguments into a
``CandidateReply``, with one bounded repair before giving up.
Out: deciding which envelopes are eligible for model rendering at all and falling back to the
template path (``app.conversation.reply``), building ``SlotValues`` from the envelope
(``app.conversation.slot_values``), and checking the candidate against them
(``app.conversation.verifier``).

Design Principles
-----------------
- The model never sees a grounded value: the prompt states only the intent and the field *names*
  this envelope allows and requires, never a fact, a decision or a source. There is nothing here
  for the model to already know a figure from.
- One bounded repair, never a re-ask: a candidate whose text overruns the contract's length bound
  is truncated once and re-validated; anything else invalid, or the call itself failing, returns
  ``None`` for the caller to fall back to the fixed-wording template — the same "one question, then
  a safe default" discipline ``LlmNlu`` already uses for understanding.
- Structured output only, matching the LLM port's own design: the model is forced to call
  ``write_reply``, never asked for free text directly.

Runtime Contract
----------------
``LlmRenderer(llm, *, model, prompt=None)`` with ``render(envelope) -> CandidateReply | None``.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Mapping  # Type of the raw tool arguments

# Third-party libraries
from pydantic import ValidationError  # The contract's own bound, enforced on construction

# Local modules
from app.llm.client import CompletionRequest, LlmClient, LlmError, ToolSpec  # The port
from app.llm.prompts import PromptTemplate, load_prompt  # Versioned prompt loading and filling
from contracts.service_v1.envelope import (  # The allowed/required field vocabulary
    INTENT_ALLOWED_FIELDS,
    INTENT_REQUIRED_FIELDS,
    GroundedField,
    RenderEnvelope,
)
from contracts.service_v1.verification import CandidateReply  # What this module produces

_PROMPT_NAME = "render_v1"
_MAX_TEXT_LENGTH = 2000  # CandidateReply.raw_text's own bound; the one repair truncates to it.

_REPLY_TOOL = ToolSpec(
    name="write_reply",
    description="Record the reply to send the customer, with grounded values as placeholders.",
    input_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": _MAX_TEXT_LENGTH,
                "description": (
                    "The reply text. A grounded value is written as {{field_name}}, never as a "
                    "digit or fact composed directly."
                ),
            }
        },
        "required": ["text"],
    },
)


def _placeholder_list(fields: frozenset[GroundedField]) -> str:
    """The field names ``fields`` names, each as its own ``{{field_name}}`` placeholder."""
    if not fields:
        return "none"
    return ", ".join(sorted(f"{{{{{field.value}}}}}" for field in fields))


def _parse(tool_input: Mapping[str, object]) -> CandidateReply | None:
    """Validate the tool's arguments into a ``CandidateReply``, with one bounded repair attempt."""
    text = tool_input.get("text")
    if not isinstance(text, str):
        return None
    try:
        return CandidateReply(raw_text=text)
    except ValidationError:
        pass
    try:
        return CandidateReply(raw_text=text[:_MAX_TEXT_LENGTH])
    except ValidationError:
        return None


class LlmRenderer:
    """Writes one candidate reply through the LLM port."""

    def __init__(self, llm: LlmClient, *, model: str, prompt: PromptTemplate | None = None) -> None:
        """
        Parameters
        ----------
        llm : LlmClient
            The port to call; a real adapter or ``FakeLlm`` in tests.
        model : str
            A model id from the configuration allow-list (the composition root's job to check).
        prompt : PromptTemplate | None
            The prompt to use; loaded from ``prompts/render_v1.yaml`` when omitted.
        """
        self._llm = llm
        self._model = model
        self._prompt = prompt or load_prompt(_PROMPT_NAME)

    def render(self, envelope: RenderEnvelope) -> CandidateReply | None:
        """Write a candidate reply for ``envelope``, or ``None`` for the caller to fall back on.

        No customer text ever reaches this call: everything the prompt states comes from the
        envelope's own intent and its closed set of allowed and required field names.
        """
        allowed = INTENT_ALLOWED_FIELDS[envelope.intent]
        required = INTENT_REQUIRED_FIELDS[envelope.intent]
        user_text = self._prompt.render_task(
            language=envelope.lang,
            intent=envelope.intent.value,
            allowed_fields=_placeholder_list(allowed),
            required_fields=_placeholder_list(required),
        )
        request = CompletionRequest(
            model=self._model,
            system=self._prompt.system,
            user_text=user_text,
            tool=_REPLY_TOOL,
            prompt_version=self._prompt.version,
            temperature=0.0,
        )
        try:
            result = self._llm.complete(request)
        except LlmError:
            return None
        return _parse(result.tool_input)
