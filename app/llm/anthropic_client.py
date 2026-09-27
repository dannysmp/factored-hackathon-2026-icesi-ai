"""
Anthropic Adapter
=================

Overview
--------
The ``LlmClient`` implementation behind the Anthropic API (ADR-7): one structured-output call,
with a forced tool, a per-call timeout and the accounting the port promises.

Scope
-----
In: translating one ``CompletionRequest`` into an Anthropic API call and its response into a
``CompletionResult``.
Out: bounded retries and circuit breaking are a later addition — this adapter makes exactly one
attempt per call and raises on failure, so a caller's own fallback path (unusable understanding,
then a person) is what absorbs a single failed call until that addition lands. Building the
request's system and user text is the caller's job, through ``app.llm.prompts`` and
``app.llm.masking``; this module never sees raw customer text.

Design Principles
-----------------
- One call, one forced tool: ``tool_choice`` names the tool, so the model cannot answer in free
  text instead of calling it.
- The timeout is per-call, from the request, never the SDK's default.
- Every failure the provider itself can raise splits three ways, because a future caller adding
  retries needs to tell them apart: a reason that has nothing to do with the request — a timeout, a
  connection error, a rate limit, a server error — becomes ``LlmUnavailable`` (safe to retry); the
  provider rejecting the request itself — bad credentials, a malformed request, no access to the
  model — becomes ``LlmRequestRejected`` (retrying the identical request fails the same way again,
  no matter how many times); a response that completed without calling the forced tool becomes
  ``LlmOutputInvalid``. No vendor exception type crosses this module's boundary.
- The API key is a value this module is handed, already resolved by the composition root through
  ``app.config``; this module never reads the environment or a secrets store itself.

Runtime Contract
----------------
``AnthropicLlmClient(api_key, *, client=None)`` implementing ``app.llm.client.LlmClient``.
"""

from __future__ import annotations

# Standard libraries
import time  # Per-call latency

# Third-party libraries
import anthropic  # The provider SDK, used only behind this adapter
from pydantic import SecretStr  # The API key, handed in already resolved

# Local modules
from app.llm.client import (  # The port this adapter implements
    CompletionRequest,
    CompletionResult,
    LlmOutputInvalid,
    LlmRequestRejected,
    LlmUnavailable,
)

# Failures the provider itself raises that mean "try again later, unrelated to this request's
# content" — never a validation or authentication failure, which no retry would fix.
_RETRYABLE_PROVIDER_ERRORS = (
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)

# Failures the provider raises because it rejected the request itself — a credential, permission
# or shape problem a retry of the identical request would never fix, unlike the errors above.
_NON_RETRYABLE_STATUS_ERRORS = (
    anthropic.AuthenticationError,
    anthropic.BadRequestError,
    anthropic.PermissionDeniedError,
    anthropic.NotFoundError,
    anthropic.UnprocessableEntityError,
)


class AnthropicLlmClient:
    """``LlmClient`` backed by the Anthropic API."""

    def __init__(self, api_key: SecretStr, *, client: anthropic.Anthropic | None = None) -> None:
        """
        Parameters
        ----------
        api_key : SecretStr
            The Anthropic API key, already resolved by the composition root
            (``Settings.require_anthropic_key()``); this adapter does not read it from the
            environment.
        client : anthropic.Anthropic | None
            An existing SDK client to use instead of constructing one; tests inject a stub here
            so no test call reaches the network.
        """
        self._client = client or anthropic.Anthropic(api_key=api_key.get_secret_value())

    def complete(self, request: CompletionRequest) -> CompletionResult:
        """Run one structured-output call against the configured model.

        Raises
        ------
        LlmUnavailable
            A timeout, connection failure, rate limit or server error; safe to retry.
        LlmRequestRejected
            The provider rejected the request itself (bad credentials, a malformed request, no
            access to the model); not safe to retry as-is.
        LlmOutputInvalid
            The model responded without calling the forced tool, or its arguments were not a
            JSON object.
        """
        started = time.monotonic()
        try:
            response = self._client.messages.create(
                model=request.model,
                system=request.system,
                messages=[{"role": "user", "content": request.user_text}],
                tools=[
                    {
                        "name": request.tool.name,
                        "description": request.tool.description,
                        "input_schema": dict(request.tool.input_schema),
                    }
                ],
                tool_choice={"type": "tool", "name": request.tool.name},
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                timeout=request.timeout_seconds,
            )
        except _RETRYABLE_PROVIDER_ERRORS as error:
            raise LlmUnavailable(f"Anthropic call failed: {type(error).__name__}") from error
        except _NON_RETRYABLE_STATUS_ERRORS as error:
            raise LlmRequestRejected(
                f"Anthropic call failed: status {error.status_code}"
            ) from error
        except anthropic.APIStatusError as error:
            # Any other status the provider might raise (for example a conflict): treated as
            # transient rather than assumed permanent, since it names no known rejection reason.
            raise LlmUnavailable(f"Anthropic call failed: status {error.status_code}") from error
        latency_ms = (time.monotonic() - started) * 1000

        tool_input = _extract_tool_input(response, request.tool.name)
        usage = response.usage
        return CompletionResult(
            tool_input=tool_input,
            model=response.model,
            prompt_version=request.prompt_version,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=latency_ms,
        )


def _extract_tool_input(response: anthropic.types.Message, tool_name: str) -> dict[str, object]:
    """The forced tool's arguments from ``response``.

    The SDK itself guarantees a ``ToolUseBlock``'s ``input`` is a JSON object (that is the field's
    own type); what this function still has to check is that the model actually called the tool
    at all, which ``tool_choice`` forces but a response is never trusted to have honored.

    Raises
    ------
    LlmOutputInvalid
        The response holds no call to ``tool_name``.
    """
    for block in response.content:
        if isinstance(block, anthropic.types.ToolUseBlock) and block.name == tool_name:
            return block.input
    raise LlmOutputInvalid(f"Anthropic response did not call the forced tool {tool_name!r}")
