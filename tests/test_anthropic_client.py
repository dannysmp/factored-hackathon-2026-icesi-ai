"""
Anthropic Adapter Tests
========================

Component: ``app.llm.anthropic_client``. Hermetic: a stub SDK client stands in for
``anthropic.Anthropic``, so no test call reaches the network. The stub only needs to look like the
SDK object this adapter actually touches (``.messages.create(...)``), not be one.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from pydantic import SecretStr

from app.llm.anthropic_client import AnthropicLlmClient
from app.llm.client import CompletionRequest, LlmOutputInvalid, LlmUnavailable, ToolSpec

_TOOL = ToolSpec(
    name="record_understanding", description="Record it.", input_schema={"type": "object"}
)
_REQUEST = CompletionRequest(
    model="claude-haiku-4-5-20251001",
    system="system prompt",
    user_text="hola",
    tool=_TOOL,
    prompt_version="1",
)


class _StubMessages:
    """Stands in for ``anthropic.Anthropic().messages``: returns or raises exactly one scripted
    outcome and records the call it was given."""

    def __init__(self, outcome: object) -> None:
        self._outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _StubAnthropic:
    def __init__(self, outcome: object) -> None:
        self.messages = _StubMessages(outcome)


def _tool_use_block(arguments: dict[str, object]) -> anthropic.types.ToolUseBlock:
    return anthropic.types.ToolUseBlock(
        id="tool_1", input=arguments, name="record_understanding", type="tool_use"
    )


def _tool_use_response(arguments: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        model="claude-haiku-4-5-20251001",
        content=[_tool_use_block(arguments)],
        usage=SimpleNamespace(input_tokens=42, output_tokens=7),
    )


def _http_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status_code=status_code, request=request)


def test_a_successful_tool_call_becomes_a_completion_result() -> None:
    stub = _StubAnthropic(_tool_use_response({"intent": "farewell", "confidence": 0.9}))
    client = AnthropicLlmClient(SecretStr("test-key"), client=stub)  # type: ignore[arg-type]

    result = client.complete(_REQUEST)

    assert result.tool_input == {"intent": "farewell", "confidence": 0.9}
    assert result.model == "claude-haiku-4-5-20251001"
    assert result.prompt_version == "1"
    assert result.input_tokens == 42
    assert result.output_tokens == 7
    assert result.latency_ms >= 0


def test_the_call_forces_the_requested_tool_and_carries_the_timeout() -> None:
    stub = _StubAnthropic(_tool_use_response({"intent": "farewell", "confidence": 0.9}))
    client = AnthropicLlmClient(SecretStr("test-key"), client=stub)  # type: ignore[arg-type]

    client.complete(_REQUEST)

    call = stub.messages.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "record_understanding"}
    assert call["tools"][0]["name"] == "record_understanding"
    assert call["timeout"] == _REQUEST.timeout_seconds
    assert call["temperature"] == 0.0
    assert call["messages"] == [{"role": "user", "content": "hola"}]


def test_a_response_that_does_not_call_the_tool_is_output_invalid() -> None:
    text_block = anthropic.types.TextBlock(citations=None, text="I cannot help.", type="text")
    response = SimpleNamespace(
        model="claude-haiku-4-5-20251001",
        content=[text_block],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    stub = _StubAnthropic(response)
    client = AnthropicLlmClient(SecretStr("test-key"), client=stub)  # type: ignore[arg-type]

    with pytest.raises(LlmOutputInvalid):
        client.complete(_REQUEST)


def test_a_tool_call_for_a_different_tool_is_output_invalid() -> None:
    other_tool = anthropic.types.ToolUseBlock(
        id="tool_1", input={"x": 1}, name="some_other_tool", type="tool_use"
    )
    response = SimpleNamespace(
        model="claude-haiku-4-5-20251001",
        content=[other_tool],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    stub = _StubAnthropic(response)
    client = AnthropicLlmClient(SecretStr("test-key"), client=stub)  # type: ignore[arg-type]

    with pytest.raises(LlmOutputInvalid):
        client.complete(_REQUEST)


def test_a_timeout_becomes_llm_unavailable() -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    stub = _StubAnthropic(anthropic.APITimeoutError(request=request))
    client = AnthropicLlmClient(SecretStr("test-key"), client=stub)  # type: ignore[arg-type]

    with pytest.raises(LlmUnavailable):
        client.complete(_REQUEST)


def test_a_rate_limit_becomes_llm_unavailable() -> None:
    stub = _StubAnthropic(
        anthropic.RateLimitError("rate limited", response=_http_response(429), body=None)
    )
    client = AnthropicLlmClient(SecretStr("test-key"), client=stub)  # type: ignore[arg-type]

    with pytest.raises(LlmUnavailable):
        client.complete(_REQUEST)


def test_a_server_error_becomes_llm_unavailable() -> None:
    stub = _StubAnthropic(
        anthropic.InternalServerError("server error", response=_http_response(500), body=None)
    )
    client = AnthropicLlmClient(SecretStr("test-key"), client=stub)  # type: ignore[arg-type]

    with pytest.raises(LlmUnavailable):
        client.complete(_REQUEST)


def test_a_non_retryable_status_error_still_surfaces_as_unavailable_this_slice() -> None:
    """Bounded retries and circuit breaking are a later change (streams.md 2.8); today every
    provider-side failure reaches the caller's single-failure fallback the same way."""
    stub = _StubAnthropic(
        anthropic.APIStatusError("bad request", response=_http_response(400), body=None)
    )
    client = AnthropicLlmClient(SecretStr("test-key"), client=stub)  # type: ignore[arg-type]

    with pytest.raises(LlmUnavailable):
        client.complete(_REQUEST)
