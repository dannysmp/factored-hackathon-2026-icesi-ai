"""
LLM Provider Selection Tests
=============================

Component: ``app.main._understanding`` and ``app.main._model_renderer``. Hermetic: no network,
no database, no ``.env``. Both take the shared ``llm_client`` ``_controller_factory`` builds
once (E9); every case here either never touches it (the stub branch) or raises before it would
be touched, so a placeholder stands in for it without needing a real or fake ``LlmClient``.
"""

from __future__ import annotations

# Standard libraries
from typing import cast

# Third-party libraries
import pytest  # Fixtures and monkeypatch

# Local modules
import app.main as main_module
from app.config import ConfigError, load_settings
from app.conversation.understanding import FakeNlu
from app.llm.client import LlmClient

_UNUSED_LLM_CLIENT = cast(LlmClient, object())


def test_the_stub_provider_builds_a_fake_understanding_with_no_key_and_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stub provider needs no ``ANTHROPIC_API_KEY`` at all."""
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    settings = load_settings(env_file=None)

    understanding = main_module._understanding(_UNUSED_LLM_CLIENT, settings)

    assert isinstance(understanding, FakeNlu)


def test_a_provider_with_no_adapter_still_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stub branch is additive: an unimplemented real provider still raises as before."""
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    settings = load_settings(env_file=None)

    with pytest.raises(ConfigError, match="'bedrock' LLM provider has no adapter yet"):
        main_module._understanding(_UNUSED_LLM_CLIENT, settings)


def test_model_rendering_under_the_stub_provider_fails_closed_not_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_model_renderer`` has no stub branch: it still requires the Anthropic provider, so
    enabling model rendering with ``LLM_PROVIDER=stub`` refuses loudly rather than doing nothing
    or reaching a provider it was never told to call."""
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("MODEL_RENDERER_ENABLED", "true")
    settings = load_settings(env_file=None)

    with pytest.raises(ConfigError, match="'stub' LLM provider has no adapter yet"):
        main_module._model_renderer(_UNUSED_LLM_CLIENT, settings)
