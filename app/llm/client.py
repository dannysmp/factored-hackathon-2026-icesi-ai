"""
LLM Port
========

Overview
--------
The one interface every caller of a language model depends on, and a scripted fake that
implements it with no network access. The port is deliberately narrow: one call, one forced tool,
one structured result. Business code never imports a vendor SDK; it imports ``LlmClient``.

Scope
-----
In: the port, its request and result shapes, the error hierarchy that tells a caller what is safe
to retry, and ``FakeLlm`` for tests and CI.
Out: the Anthropic-backed adapter (``anthropic_client``), the egress masking every request passes
through first (``masking``), and turning a result into a typed domain model (the caller's job —
the port returns the tool call's raw arguments, not a validated ``NluResult``).

Design Principles
-----------------
- Structured output only: a caller always forces exactly one tool and reads back its arguments.
  Free-form text completion is not part of this port; nothing in the service renders free-form
  model text today (the renderer is template-only until an output verifier exists).
- The error hierarchy separates what a caller may retry (``LlmUnavailable``: timeout, rate limit,
  a 5xx) from what it must not (``LlmOutputInvalid``: the model did not call the tool, or its
  arguments do not parse as JSON — retrying the exact same request would not help; the caller's
  own repair-then-fallback path, not this port, decides whether to re-ask).
- Accounting travels with every result (tokens, latency, the model and prompt version used) so the
  caller can log it without a second call.
- ``FakeLlm`` is scripted, not clever: it returns exactly what the test queues, in order, so a test
  reads as the conversation it describes.

Runtime Contract
----------------
``LlmClient`` (protocol): ``complete(request) -> CompletionResult``.
``CompletionRequest``, ``ToolSpec``, ``CompletionResult``.
``LlmError``, ``LlmUnavailable``, ``LlmOutputInvalid``.
``FakeLlm``: a scripted implementation with no network access.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Mapping, Sequence  # Types of tool schemas and scripted responses
from dataclasses import dataclass, field  # Immutable request/result shapes
from typing import Protocol  # The port itself


class LlmError(Exception):
    """Base of every failure this port raises."""


class LlmUnavailable(LlmError):
    """The provider could not complete the call this time: a timeout, a rate limit, a 5xx.

    Safe to retry with bounded backoff; retrying the identical request may succeed.
    """


class LlmOutputInvalid(LlmError):
    """The model did not call the forced tool, or its arguments do not parse as JSON.

    Not safe to retry as-is: the caller's structured-output path (validate, repair, fall back)
    decides whether to re-ask with the validation error included, not this port.
    """


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """The one tool a call forces the model to use, describing the shape of its arguments."""

    name: str
    description: str
    input_schema: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """One structured-output call.

    Attributes
    ----------
    model : str
        A model id from the configuration allow-list; the port does not itself check membership,
        the composition root does when it builds the adapter.
    system : str
        The system prompt, already filled from a versioned prompt file.
    user_text : str
        The turn-specific content, already passed through the masking serializer.
    tool : ToolSpec
        The single tool the model must call.
    prompt_version : str
        Recorded with the result so every logged call carries the prompt version that produced it.
    max_tokens : int
        Output token budget.
    temperature : float
        Sampling temperature; structured extraction and routing run at ``0.0``.
    timeout_seconds : float
        Per-call timeout the adapter enforces.
    """

    model: str
    system: str
    user_text: str
    tool: ToolSpec
    prompt_version: str
    max_tokens: int = 1024
    temperature: float = 0.0
    timeout_seconds: float = 8.0


@dataclass(frozen=True, slots=True)
class CompletionResult:
    """What one completed call produced, with the accounting every log line needs.

    Attributes
    ----------
    tool_input : Mapping[str, object]
        The forced tool's arguments, parsed as JSON; not yet validated against a domain schema.
    model : str
        The model id that actually served the call.
    prompt_version : str
        Carried over from the request, so a log line needs only the result.
    input_tokens, output_tokens : int
        Token accounting for cost tracking.
    latency_ms : float
        Wall-clock time the call took.
    """

    tool_input: Mapping[str, object]
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class LlmClient(Protocol):
    """The port every caller of a language model depends on."""

    def complete(self, request: CompletionRequest) -> CompletionResult:
        """Run one structured-output call.

        Raises
        ------
        LlmUnavailable
            The provider could not complete the call this time; safe to retry.
        LlmOutputInvalid
            The model's output does not parse as a tool call with JSON arguments.
        """
        ...


@dataclass(slots=True)
class FakeLlm:
    """A scripted ``LlmClient``: returns exactly what a test queued, in order, with no network.

    Every request is recorded on ``requests`` before it is answered, so a test can assert what a
    caller sent (the masked text, the model id, the forced tool) as well as what it got back.

    Parameters
    ----------
    responses : Sequence[Mapping[str, object] | LlmError]
        One entry per expected call: a mapping of tool arguments to return, or an ``LlmError``
        instance to raise. Consumed in order; a call beyond the script is a test bug, reported as
        ``LlmOutputInvalid`` rather than an ``IndexError`` so a caller's fallback path is exercised
        the same way a real exhausted-retry failure would exercise it.
    """

    responses: Sequence[Mapping[str, object] | LlmError]
    requests: list[CompletionRequest] = field(default_factory=list)
    _next: int = 0

    def complete(self, request: CompletionRequest) -> CompletionResult:
        """Return the next scripted response, or raise it."""
        self.requests.append(request)
        if self._next >= len(self.responses):
            raise LlmOutputInvalid("FakeLlm: no scripted response left for this call")
        response = self.responses[self._next]
        self._next += 1
        if isinstance(response, LlmError):
            raise response
        return CompletionResult(
            tool_input=response,
            model=request.model,
            prompt_version=request.prompt_version,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0.0,
        )
