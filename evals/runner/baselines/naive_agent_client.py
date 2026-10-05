"""
Naive Agent LLM Client
======================

Overview
--------
A narrow Anthropic SDK wrapper for the B1 baseline, a naive LLM agent: the same model and tools in
one prompt, with no policy engine, verifier or output checks. B1 needs the model to choose freely
whether to call a tool, call several in sequence, or reply in text. That is the opposite of what
``app.llm.client.LlmClient`` is built for, whose decide-then-render grounding forces exactly one
tool per call. This module is a separate adapter for the different call shape; it does not
implement ``LlmClient`` and is never reached from a request path that serves a customer.

Scope
-----
In: ``NaiveAgentClient``, one open-tool-choice call to the Anthropic API, and the turn it returns
(the text said and the tool calls made, if any).
Out: the conversation loop that drives several turns, decides when to stop and dispatches a called
tool (``evals.runner.baselines.b1``); the tool schemas (``evals.runner.baselines.b1_tools``);
scoring a B1 run (``evals.scoring``).

Design Principles
-----------------
- **A separate adapter, not a bent ``LlmClient``.** Reusing ``LlmClient`` or ``AnthropicLlmClient``
  would mean forcing a tool B1 must be free not to call, or weakening the production port's
  forced-tool guarantee for every other caller. A second narrow implementation under ``evals/``
  keeps that guarantee intact.
- **The defensive call shape of the production adapter.** A per-call timeout, and the same split
  between a transient provider failure (``LlmUnavailable``), a rejected request
  (``LlmRequestRejected``) and no vendor exception crossing the module boundary. It reuses the
  exception types of ``app.llm.client`` because the failure taxonomy belongs to calling the
  provider, not to the forced-tool shape ``LlmClient`` adds.
- **A request too large for the provider is its own, narrower rejection.**
  ``NaiveAgentRequestTooLarge`` subclasses ``LlmRequestRejected``, so existing handlers still treat
  it as a rejection, but a caller sequencing a batch can tell it from account-level causes (bad
  credentials, no model access): it is driven by one case's conversation and does not recur for the
  next case.
- **The model id is checked against the shared allow-list.** B1 is not given a looser model policy
  than P.

Runtime Contract
----------------
``NaiveAgentClient(api_key, *, model, client=None)``.
``send(messages, tools, *, system, max_tokens, timeout_seconds) -> NaiveAgentTurn``.

Limitations
-----------
The client returns one turn; looping until the model stops calling tools and dispatching each
called tool belong to ``evals.runner.baselines.b1``. No tool schema is defined here: the module
only places a list of tools in front of the model and reads back what it did.
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
from app.config import ALLOWED_MODELS  # The model-id allow-list, shared with P and B0
from app.llm.client import (  # Reused: the failure taxonomy is provider-level, not LlmClient-only
    LlmRequestRejected,
    LlmUnavailable,
)

# The split ``app.llm.anthropic_client`` draws: failures a caller could retry unchanged, and those
# it could not.
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
)


class NaiveAgentRequestTooLarge(LlmRequestRejected):
    """The request exceeded the provider's byte limit (HTTP 413).

    A rejected request like any other, so it is never retried, but unlike a credential or access
    problem it is driven by one case's own accumulated conversation, not the account: the next
    case's request is unaffected. Callers that sequence a batch record it against the case instead
    of stopping the run.
    """


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool the model asked to call, with the arguments it gave.

    ``id`` is the provider's identifier for the call, echoed back with the tool result.
    """

    id: str
    name: str
    input: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class NaiveAgentTurn:
    """What the model did on one call: whatever text it said, and every tool call it made.

    ``text`` joins every text block with newlines and is empty when the model only called tools.
    ``latency_ms`` is the wall-clock duration of the call; the token counts are the provider's.
    """

    text: str
    tool_calls: tuple[ToolCall, ...]
    stop_reason: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class NaiveAgentClient:
    """A single, open-tool-choice call to the Anthropic API, for the B1 baseline only.

    The client holds no conversation state: each ``send`` is given the whole message list.
    """

    def __init__(
        self, api_key: SecretStr, *, model: str, client: anthropic.Anthropic | None = None
    ) -> None:
        """
        Parameters
        ----------
        api_key : SecretStr
            The Anthropic API key, already resolved by the caller.
        model : str
            Must be a member of ``app.config.ALLOWED_MODELS``; the same allow-list every other
            caller in this project is pinned to.
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
        """Run one call with ``messages`` so far and ``tools`` available.

        The model may call zero, one or several tools, or reply in text alone. ``system`` is the
        system prompt, ``max_tokens`` bounds the reply and ``timeout_seconds`` the wait.

        Raises
        ------
        LlmUnavailable
            A timeout, connection failure, rate limit or server error; safe to retry.
        NaiveAgentRequestTooLarge
            The request exceeded the provider's byte limit; a case-scoped ``LlmRequestRejected``.
        LlmRequestRejected
            The provider rejected the request itself; not safe to retry as-is.
        """
        started = time.monotonic()
        try:
            # The SDK's parameter types are precise TypedDicts this wrapper does not re-declare;
            # the B1 loop builds messages and tools already shaped to the API's contract.
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
        except anthropic.RequestTooLargeError as error:
            raise NaiveAgentRequestTooLarge(
                f"Anthropic call failed: status {error.status_code}"
            ) from error
        except _NON_RETRYABLE_STATUS_ERRORS as error:
            raise LlmRequestRejected(
                f"Anthropic call failed: status {error.status_code}"
            ) from error
        except anthropic.APIStatusError as error:
            raise LlmUnavailable(f"Anthropic call failed: status {error.status_code}") from error
        # Rounded rather than left at full float precision: a long unrounded duration can look like
        # a card number to the log redaction (see ``app.llm.anthropic_client``).
        latency_ms = round((time.monotonic() - started) * 1000, 3)

        text = "\n".join(
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
