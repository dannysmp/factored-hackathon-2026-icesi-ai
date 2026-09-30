"""
Naive Agent LLM Client Tests
=============================

Component: ``evals.runner.baselines.naive_agent_client``. Hermetic: a stub SDK client stands in
for ``anthropic.Anthropic``, so no test call reaches the network, matching
``tests.test_anthropic_client``'s own convention for the production adapter this module deliberately
does not reuse.
"""

from __future__ import annotations

import itertools
import time
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from pydantic import SecretStr

from app.llm.client import LlmRequestRejected, LlmUnavailable
from app.llm.masking import redact_pan
from evals.runner.baselines.naive_agent_client import NaiveAgentClient

_MODEL = "claude-haiku-4-5-20251001"
_TOOLS = [{"name": "get_transaction", "description": "Look one up.", "input_schema": {}}]
_MESSAGES = [{"role": "user", "content": "hola"}]


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


def _text_block(text: str) -> anthropic.types.TextBlock:
    return anthropic.types.TextBlock(citations=None, text=text, type="text")


def _tool_use_block(name: str, arguments: dict[str, object], *, block_id: str) -> Any:
    return anthropic.types.ToolUseBlock(id=block_id, input=arguments, name=name, type="tool_use")


def _response(content: list[object], *, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        model=_MODEL,
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=42, output_tokens=7),
    )


def _http_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status_code=status_code, request=request)


def test_the_model_id_must_be_in_the_allow_list() -> None:
    with pytest.raises(ValueError, match="allow-list"):
        NaiveAgentClient(SecretStr("test-key"), model="claude-opus-4-99999")


def test_a_text_only_reply_carries_no_tool_calls() -> None:
    stub = _StubAnthropic(_response([_text_block("Hola, ¿en qué puedo ayudarle?")]))
    client = NaiveAgentClient(SecretStr("test-key"), model=_MODEL, client=stub)  # type: ignore[arg-type]

    turn = client.send(
        _MESSAGES, _TOOLS, system="system prompt", max_tokens=512, timeout_seconds=10.0
    )

    assert turn.text == "Hola, ¿en qué puedo ayudarle?"
    assert turn.tool_calls == ()
    assert turn.stop_reason == "end_turn"
    assert turn.input_tokens == 42
    assert turn.output_tokens == 7
    assert turn.latency_ms >= 0


def test_latency_is_rounded_so_it_can_never_look_card_shaped_in_a_log_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrounded duration's decimal expansion can run long enough to fall inside a real card
    number's digit-length window; the log formatter's blanket PAN redaction would then treat a
    latency figure as a leaked card number purely because enough of its digits pass the Luhn check
    by chance. ``3125.2192252088007`` is a real example: unrounded, ``redact_pan`` flags it.
    Rounding closes this off by construction: three decimal places never reach the 13-digit floor
    real PANs start at."""
    unrounded_elapsed_ms = 3125.2192252088007
    assert redact_pan(f"latency_ms={unrounded_elapsed_ms}").found  # the vulnerability, confirmed

    clock = itertools.count()
    monkeypatch.setattr(time, "monotonic", lambda: next(clock) * (unrounded_elapsed_ms / 1000))
    stub = _StubAnthropic(_response([_text_block("Hola, ¿en qué puedo ayudarle?")]))
    client = NaiveAgentClient(SecretStr("test-key"), model=_MODEL, client=stub)  # type: ignore[arg-type]

    turn = client.send(
        _MESSAGES, _TOOLS, system="system prompt", max_tokens=512, timeout_seconds=10.0
    )

    assert turn.latency_ms == round(turn.latency_ms, 3)
    assert not redact_pan(f"latency_ms={turn.latency_ms}").found


def test_a_tool_call_is_captured_with_its_id_name_and_arguments() -> None:
    stub = _StubAnthropic(
        _response(
            [_tool_use_block("get_transaction", {"ref": "tx-1"}, block_id="tool_1")],
            stop_reason="tool_use",
        )
    )
    client = NaiveAgentClient(SecretStr("test-key"), model=_MODEL, client=stub)  # type: ignore[arg-type]

    turn = client.send(
        _MESSAGES, _TOOLS, system="system prompt", max_tokens=512, timeout_seconds=10.0
    )

    assert turn.text == ""
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].id == "tool_1"
    assert turn.tool_calls[0].name == "get_transaction"
    assert turn.tool_calls[0].input == {"ref": "tx-1"}
    assert turn.stop_reason == "tool_use"


def test_several_text_blocks_join_with_a_separator_not_glued_together() -> None:
    """A bare `"".join` would run two blocks together with no boundary at all — "helloworld"
    instead of two readable spans; a newline keeps them distinguishable."""
    stub = _StubAnthropic(_response([_text_block("First span."), _text_block("Second span.")]))
    client = NaiveAgentClient(SecretStr("test-key"), model=_MODEL, client=stub)  # type: ignore[arg-type]

    turn = client.send(
        _MESSAGES, _TOOLS, system="system prompt", max_tokens=512, timeout_seconds=10.0
    )

    assert turn.text == "First span.\nSecond span."


def test_text_and_several_tool_calls_can_both_appear_in_one_turn() -> None:
    stub = _StubAnthropic(
        _response(
            [
                _text_block("Let me check that."),
                _tool_use_block("get_transaction", {"ref": "tx-1"}, block_id="tool_1"),
                _tool_use_block("list_dispute_cases", {}, block_id="tool_2"),
            ],
            stop_reason="tool_use",
        )
    )
    client = NaiveAgentClient(SecretStr("test-key"), model=_MODEL, client=stub)  # type: ignore[arg-type]

    turn = client.send(
        _MESSAGES, _TOOLS, system="system prompt", max_tokens=512, timeout_seconds=10.0
    )

    assert turn.text == "Let me check that."
    assert [call.name for call in turn.tool_calls] == ["get_transaction", "list_dispute_cases"]


def test_the_call_never_forces_a_tool_and_carries_the_given_messages_and_timeout() -> None:
    stub = _StubAnthropic(_response([_text_block("ok")]))
    client = NaiveAgentClient(SecretStr("test-key"), model=_MODEL, client=stub)  # type: ignore[arg-type]

    client.send(_MESSAGES, _TOOLS, system="system prompt", max_tokens=512, timeout_seconds=7.5)

    call = stub.messages.calls[0]
    assert call["tool_choice"] == {"type": "auto"}
    assert call["tools"] == _TOOLS
    assert call["messages"] == _MESSAGES
    assert call["timeout"] == 7.5
    assert call["max_tokens"] == 512
    assert call["system"] == "system prompt"


def test_a_timeout_becomes_llm_unavailable() -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    stub = _StubAnthropic(anthropic.APITimeoutError(request=request))
    client = NaiveAgentClient(SecretStr("test-key"), model=_MODEL, client=stub)  # type: ignore[arg-type]

    with pytest.raises(LlmUnavailable):
        client.send(_MESSAGES, _TOOLS, system="s", max_tokens=512, timeout_seconds=10.0)


@pytest.mark.parametrize(
    ("error_type", "status_code"),
    [
        (anthropic.AuthenticationError, 401),
        (anthropic.BadRequestError, 400),
        (anthropic.PermissionDeniedError, 403),
    ],
    ids=["authentication", "bad_request", "permission_denied"],
)
def test_a_non_retryable_status_error_is_a_rejected_request(
    error_type: type[anthropic.APIStatusError], status_code: int
) -> None:
    stub = _StubAnthropic(error_type("rejected", response=_http_response(status_code), body=None))
    client = NaiveAgentClient(SecretStr("test-key"), model=_MODEL, client=stub)  # type: ignore[arg-type]

    with pytest.raises(LlmRequestRejected):
        client.send(_MESSAGES, _TOOLS, system="s", max_tokens=512, timeout_seconds=10.0)


def test_an_unrecognized_status_error_still_falls_back_to_unavailable() -> None:
    stub = _StubAnthropic(
        anthropic.ConflictError("conflict", response=_http_response(409), body=None)
    )
    client = NaiveAgentClient(SecretStr("test-key"), model=_MODEL, client=stub)  # type: ignore[arg-type]

    with pytest.raises(LlmUnavailable):
        client.send(_MESSAGES, _TOOLS, system="s", max_tokens=512, timeout_seconds=10.0)
