"""
Naive Agent LLM Client
========================

Overview
--------
A narrow Anthropic SDK wrapper for the B1 baseline the evaluation plan names: "naive LLM agent:
same model + tools in one prompt; no policy engine, no verifier, no output checks." B1 needs the
model to freely choose whether to call a tool, call several in sequence, or just reply in text —
the opposite of what ``app.llm.client.LlmClient`` is built for (ADR-2's decide-then-render
grounding forces exactly one tool per call, by design). This module is a separate, narrow adapter
for exactly that different call shape; it does not implement ``LlmClient`` and is never reached
from any request path this service serves to a customer.

Scope
-----
In: ``NaiveAgentClient``, one open-tool-choice call to the Anthropic API, and the turn it returns
(text said, tool calls made, if any).
Out: the conversation loop that drives multiple turns, decides when to stop, and dispatches a
called tool against the real ``ToolPort`` (a following increment); the tool schemas themselves
(the same following increment, since they depend on the seven tools B1 exposes); scoring a B1 run
(``evals.scoring``, already built, reused unchanged once B1 produces a ``RunTranscript``).

Design Principles
-----------------
- **A separate adapter, not a bent `LlmClient`.** Reusing `LlmClient`/`AnthropicLlmClient` would
  mean either forcing a tool B1 must be free not to call, or weakening the production port's own
  forced-tool guarantee for every other caller. A second, narrow implementation under `evals/`
  keeps that guarantee intact and gives B1 exactly the call shape it needs.
- **The same defensive call shape as the production adapter, reused in spirit.** A per-call
  timeout, and the same three-way split between a transient provider failure
  (``LlmUnavailable``), a rejected request (``LlmRequestRejected``) and no vendor exception
  crossing this module's boundary — reusing the exact exception types
  ``app.llm.client`` already defines, since the failure taxonomy is a property of calling the
  provider, not of the forced-tool shape ``LlmClient`` itself adds on top.
- **The model id is pinned to the same reviewed allow-list every caller uses.** B1 is still a
  system variant this project runs cost-tracked, reviewed calls against; it does not get a
  looser model policy than P's own.

Runtime Contract
-----------------
``NaiveAgentClient(api_key, *, model, client=None)``.
``send(messages, tools, *, system, max_tokens, timeout_seconds) -> NaiveAgentTurn``.

Limitations
-----------
Returns one turn; looping until the model stops calling tools, and dispatching a called tool
against a real, session-scoped ``ToolPort``, is the following increment's job (see Scope). No tool
schema is defined here — this module knows nothing about what tools exist, only how to place a
list of them in front of the model and read back what it did.
"""

from __future__ import annotations

# Standard libraries
import time  # Per-call latency
from collections.abc import Mapping, Sequence
from dataclasses import dataclass  # Immutable results
from typing import Any, cast  # Bridging to the SDK's own precisely-typed call shape

# Third-party libraries
import anthropic  # The provider SDK, used only behind this adapter
from pydantic import SecretStr  # The API key, handed in already resolved

# Local modules
from app.config import ALLOWED_MODELS  # The reviewed model-id allow-list, shared with P and B0
from app.llm.client import (  # Reused: the failure taxonomy is provider-level, not LlmClient-only
    LlmRequestRejected,
    LlmUnavailable,
)

# Same split app.llm.anthropic_client draws, for the same reason: which failures a caller could
# retry unchanged, and which it could not.
_RETRYABLE_PROVIDER_ERRORS = (
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)
_NON_RETRYABLE_STATUS_ERRORS = (
    anthropic.AuthenticationError,
    anthropic.BadRequestError,
    anthropic.PermissionDeniedError,
    anthropic.NotFoundError,
    anthropic.UnprocessableEntityError,
    anthropic.RequestTooLargeError,
)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool the model asked to call, with the arguments it gave."""

    id: str
    name: str
    input: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class NaiveAgentTurn:
    """What the model did on one call: whatever text it said, and every tool call it made."""

    text: str
    tool_calls: tuple[ToolCall, ...]
    stop_reason: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class NaiveAgentClient:
    """A single, open-tool-choice call to the Anthropic API, for the B1 baseline only."""

    def __init__(
        self, api_key: SecretStr, *, model: str, client: anthropic.Anthropic | None = None
    ) -> None:
        """
        Parameters
        ----------
        api_key : SecretStr
            The Anthropic API key, already resolved by the caller.
        model : str
            Must be a member of ``app.config.ALLOWED_MODELS``; the same reviewed allow-list every
            other caller in this project is pinned to.
        client : anthropic.Anthropic | None
            An existing SDK client to use instead of constructing one; tests inject a stub here so
            no test call reaches the network.

        Raises
        ------
        ValueError
            ``model`` is not in the allow-list.
        """
        if model not in ALLOWED_MODELS:
            raise ValueError(f"model id is not in the allow-list: {model!r}")
        self._model = model
        self._client = client or anthropic.Anthropic(api_key=api_key.get_secret_value())

    def send(
        self,
        messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
        *,
        system: str,
        max_tokens: int,
        timeout_seconds: float,
    ) -> NaiveAgentTurn:
        """Run one call with ``messages`` so far and ``tools`` available; the model may call zero,
        one or several of them, or reply in text alone.

        Raises
        ------
        LlmUnavailable
            A timeout, connection failure, rate limit or server error; safe to retry.
        LlmRequestRejected
            The provider rejected the request itself; not safe to retry as-is.
        """
        started = time.monotonic()
        try:
            # The SDK's own parameter types are precise TypedDicts this thin wrapper does not
            # re-declare; the caller (the B1 loop, a following increment) builds messages and
            # tools already shaped to the API's own contract.
            response = self._client.messages.create(
                model=self._model,
                system=system,
                messages=cast(Any, list(messages)),
                tools=cast(Any, list(tools)),
                tool_choice={"type": "auto"},
                max_tokens=max_tokens,
                timeout=timeout_seconds,
            )
        except _RETRYABLE_PROVIDER_ERRORS as error:
            raise LlmUnavailable(f"Anthropic call failed: {type(error).__name__}") from error
        except _NON_RETRYABLE_STATUS_ERRORS as error:
            raise LlmRequestRejected(
                f"Anthropic call failed: status {error.status_code}"
            ) from error
        except anthropic.APIStatusError as error:
            raise LlmUnavailable(f"Anthropic call failed: status {error.status_code}") from error
        latency_ms = (time.monotonic() - started) * 1000

        text = "".join(
            block.text for block in response.content if isinstance(block, anthropic.types.TextBlock)
        )
        tool_calls = tuple(
            ToolCall(id=block.id, name=block.name, input=block.input)
            for block in response.content
            if isinstance(block, anthropic.types.ToolUseBlock)
        )
        usage = response.usage
        return NaiveAgentTurn(
            text=text,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            model=self._model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=latency_ms,
        )
