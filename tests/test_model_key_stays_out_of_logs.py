"""
Model Key Log Containment Tests
================================

Component: the structured log output and the exception text on the two paths that handle the model
API key at run time: a configuration failure at start-up (``app.main`` logs ``config_invalid``
and re-raises) and a call through the provider SDK (``app.llm.anthropic_client``). The key is a
static third-party secret; it must reach the provider only as the request's credential header and
nowhere in what the service writes or raises.

Design Principles
-----------------
- A sentinel of the provider key's shape is built at run time, so this file never contains a
  match for the repository scan, and every assertion looks for the whole sentinel *and* any
  twelve-character window of it, so a truncated or partly masked key still fails.
- Each path is exercised through its real code: the real settings loader and application factory
  for the configuration failure, and the real SDK client over an in-process HTTP transport for the
  provider call, so the SDK's and the HTTP library's own log lines are in the capture.
- The captured text is checked to be non-empty and to contain the expected event, so a capture
  that silently saw nothing cannot pass.

Limitations
-----------
- Only the loggers active at the level under test are captured. Production runs at ``INFO``; the
  provider-call test also runs at ``DEBUG`` as a stricter floor.
- A key that is transformed (encoded, split) before being written is not detected.
- The provider's own servers and any proxy in front of them are outside what this can see.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import anthropic
import httpx
import pytest
from pydantic import SecretStr

from app.config import ConfigError, Settings
from app.llm.anthropic_client import AnthropicLlmClient
from app.llm.client import CompletionRequest, LlmRequestRejected, ToolSpec
from app.main import create_app
from app.observability.logging import configure_logging

_SENTINEL = "sk-ant-" + "api03-" + "A1b2C3d4" * 5
_WINDOW = 12
_MODEL = "claude-haiku-4-5-20251001"
_TOOL = ToolSpec(
    name="record",
    description="Record the result.",
    input_schema={"type": "object", "properties": {}},
)


def _key_fragments() -> list[str]:
    return [_SENTINEL, *{_SENTINEL[i : i + _WINDOW] for i in range(len(_SENTINEL) - _WINDOW + 1)}]


def _assert_no_key(text: str) -> None:
    for fragment in _key_fragments():
        assert fragment not in text, f"part of the model key reached the output: {fragment[:6]}..."


def _request() -> CompletionRequest:
    return CompletionRequest(
        model=_MODEL,
        system="Extract the fields.",
        user_text="hello",
        tool=_TOOL,
        prompt_version="test-v1",
        temperature=0.0,
        max_tokens=64,
        timeout_seconds=5.0,
    )


def _message_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": _MODEL,
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "content": [{"type": "tool_use", "id": "tu_1", "name": "record", "input": {}}],
            "usage": {"input_tokens": 3, "output_tokens": 2},
        },
    )


def _rejection_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        401,
        json={
            "type": "error",
            "error": {"type": "authentication_error", "message": "invalid x-api-key"},
        },
    )


def _sdk_client(handler: httpx.MockTransport) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=_SENTINEL, http_client=httpx.Client(transport=handler), max_retries=0
    )


def test_the_fragment_check_fails_on_a_whole_a_partial_and_a_clean_text() -> None:
    with pytest.raises(AssertionError):
        _assert_no_key(f"auth failed for {_SENTINEL}")
    with pytest.raises(AssertionError):
        _assert_no_key(f"auth failed for {_SENTINEL[:20]}")
    with pytest.raises(AssertionError):
        _assert_no_key(f"auth failed for {_SENTINEL[-15:]}")
    _assert_no_key("auth failed for sk-ant-...")


def test_a_configuration_failure_logs_and_raises_without_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", _SENTINEL)
    # The sentinel is also the invalid value, the case where a naive error would echo its input.
    monkeypatch.setenv("APP_ENV", _SENTINEL)

    with pytest.raises(ConfigError) as raised:
        create_app()

    captured = capsys.readouterr()
    assert "config_invalid" in captured.err
    assert "APP_ENV" in captured.err
    _assert_no_key(captured.err)
    _assert_no_key(captured.out)
    _assert_no_key(str(raised.value))
    _assert_no_key(repr(raised.value))


def test_the_settings_object_does_not_print_the_key() -> None:
    settings = Settings(anthropic_api_key=SecretStr(_SENTINEL), _env_file=None)

    assert settings.anthropic_api_key is not None
    _assert_no_key(repr(settings))
    _assert_no_key(str(settings))
    _assert_no_key(settings.model_dump_json())
    _assert_no_key(repr(settings.require_anthropic_key()))


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_a_successful_provider_call_logs_without_the_key(
    level: str, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(level, service_version="test-sha", environment="local")
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        return _message_response(request)

    sdk = _sdk_client(httpx.MockTransport(handler))

    result = AnthropicLlmClient(SecretStr(_SENTINEL), client=sdk).complete(_request())

    assert result.model == _MODEL
    assert seen_headers[0]["x-api-key"] == _SENTINEL
    captured = capsys.readouterr()
    assert "HTTP Request" in captured.err
    if level == "DEBUG":
        assert "Request options" in captured.err
    _assert_no_key(captured.err)
    _assert_no_key(captured.out)


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_a_rejected_provider_call_logs_and_raises_without_the_key(
    level: str, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(level, service_version="test-sha", environment="local")
    sdk = _sdk_client(httpx.MockTransport(_rejection_response))

    with pytest.raises(LlmRequestRejected) as raised:
        AnthropicLlmClient(SecretStr(_SENTINEL), client=sdk).complete(_request())

    captured = capsys.readouterr()
    assert "HTTP Request" in captured.err
    _assert_no_key(captured.err)
    _assert_no_key(captured.out)
    _assert_no_key(str(raised.value))
    _assert_no_key(repr(raised.value.__cause__))


@pytest.fixture(autouse=True)
def _restore_root_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)
