"""
LLM Provider Selection Tests
=============================

Component: ``app.main._understanding``. Hermetic: no network, no database, no ``.env``.
"""

from __future__ import annotations

# Third-party libraries
import pytest  # Fixtures and monkeypatch

# Local modules
import app.main as main_module
from app.config import ConfigError, load_settings
from app.conversation.understanding import FakeNlu


def test_the_stub_provider_builds_a_fake_understanding_with_no_key_and_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stub provider needs no ``ANTHROPIC_API_KEY`` at all."""
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    settings = load_settings(env_file=None)

    understanding = main_module._understanding(settings)

    assert isinstance(understanding, FakeNlu)


def test_a_provider_with_no_adapter_still_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stub branch is additive: an unimplemented real provider still raises as before."""
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    settings = load_settings(env_file=None)

    with pytest.raises(ConfigError, match="'bedrock' LLM provider has no adapter yet"):
        main_module._understanding(settings)
