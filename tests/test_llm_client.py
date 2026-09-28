"""
LLM Port Tests
==============

Component: ``app.llm.client``. Hermetic and pure: ``FakeLlm`` makes no network call.
"""

from __future__ import annotations

import pytest

from app.llm.client import (
    CompletionRequest,
    FakeLlm,
    LlmOutputInvalid,
    LlmUnavailable,
    ToolSpec,
)

_TOOL = ToolSpec(name="record", description="Record something.", input_schema={"type": "object"})


def _request(user_text: str = "hello") -> CompletionRequest:
    return CompletionRequest(
        model="claude-haiku-4-5-20251001",
        system="system prompt",
        user_text=user_text,
        tool=_TOOL,
        prompt_version="1",
    )


def test_fake_llm_returns_scripted_responses_in_order() -> None:
    """Each call consumes the next queued response, not a random one."""
    fake = FakeLlm(responses=[{"intent": "farewell"}, {"intent": "small_talk"}])

    first = fake.complete(_request(user_text="adiós"))
    second = fake.complete(_request(user_text="hola"))

    assert first.tool_input == {"intent": "farewell"}
    assert second.tool_input == {"intent": "small_talk"}


def test_fake_llm_records_every_request_it_was_given() -> None:
    """A test can assert what a caller sent, not only what it got back."""
    fake = FakeLlm(responses=[{"intent": "farewell"}])

    fake.complete(_request(user_text="chau"))

    assert len(fake.requests) == 1
    assert fake.requests[0].user_text == "chau"


def test_fake_llm_raises_a_scripted_error() -> None:
    """A scripted ``LlmError`` is raised instead of returning a result."""
    fake = FakeLlm(responses=[LlmUnavailable("provider timed out")])

    with pytest.raises(LlmUnavailable):
        fake.complete(_request())


def test_fake_llm_raises_when_the_script_is_exhausted() -> None:
    """A call beyond the script is a test bug, reported the same way an exhausted retry would be."""
    fake = FakeLlm(responses=[{"intent": "farewell"}])
    fake.complete(_request())

    with pytest.raises(LlmOutputInvalid):
        fake.complete(_request())


def test_completion_request_defaults_are_deterministic_extraction_settings() -> None:
    """Structured extraction runs at temperature 0 unless a caller overrides it."""
    request = _request()

    assert request.temperature == 0.0
    assert request.max_tokens > 0
    assert request.timeout_seconds > 0
