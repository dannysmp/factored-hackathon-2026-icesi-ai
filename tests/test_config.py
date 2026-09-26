"""
Configuration Tests
===================

Component: ``app.config``. Hermetic: environment is set through ``monkeypatch`` and no
``.env`` file is read (``env_file=None`` or a temp file).
Out of scope: how other modules consume settings.
"""

from __future__ import annotations

# Standard libraries
from pathlib import Path  # Temporary dotenv files

# Third-party libraries
import pytest  # Test runner, parametrization and monkeypatch

# Local modules
from app.config import (
    ALLOWED_MODELS,
    AppEnvironment,
    ConfigError,
    LlmProvider,
    LogLevel,
    load_settings,
)

_ENV_KEYS = (
    "APP_ENV",
    "LOG_LEVEL",
    "SERVICE_VERSION",
    "LLM_PROVIDER",
    "NLU_MODEL",
    "RENDER_MODEL",
    "ANTHROPIC_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from an environment with none of the service's variables set."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_defaults_are_valid_and_pinned() -> None:
    """A bare environment yields the documented defaults, all inside the allow-list."""
    settings = load_settings(env_file=None)

    assert settings.app_env is AppEnvironment.LOCAL
    assert settings.log_level is LogLevel.INFO
    assert settings.llm_provider is LlmProvider.ANTHROPIC
    assert settings.nlu_model in ALLOWED_MODELS
    assert settings.render_model in ALLOWED_MODELS
    assert settings.anthropic_api_key is None


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables take effect and are parsed into enums."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("SERVICE_VERSION", "abc1234")
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")

    settings = load_settings(env_file=None)

    assert settings.app_env is AppEnvironment.PROD
    assert settings.log_level is LogLevel.WARNING
    assert settings.service_version == "abc1234"
    assert settings.llm_provider is LlmProvider.BEDROCK


def test_env_file_is_read_when_present(tmp_path: Path) -> None:
    """A dotenv file supplies values, and unknown keys in it are ignored."""
    env_file = tmp_path / ".env"
    env_file.write_text("SERVICE_VERSION=from-file\nUNRELATED_KEY=1\n")

    assert load_settings(env_file=env_file).service_version == "from-file"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("APP_ENV", "staging"),
        ("LOG_LEVEL", "verbose"),
        ("LLM_PROVIDER", "openai"),
        ("NLU_MODEL", "gpt-4o"),
        ("RENDER_MODEL", "claude-sonnet-4"),
    ],
    ids=["environment", "log-level", "provider", "nlu-model", "render-model"],
)
def test_invalid_value_fails_fast_naming_the_key(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    """Invalid values raise ConfigError that names the key but never echoes the value."""
    monkeypatch.setenv(key, value)

    with pytest.raises(ConfigError) as excinfo:
        load_settings(env_file=None)

    assert key in str(excinfo.value)
    assert value not in str(excinfo.value)


def test_multiple_invalid_keys_are_all_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """One error lists every bad key so a deployment can be fixed in a single pass."""
    monkeypatch.setenv("LOG_LEVEL", "verbose")
    monkeypatch.setenv("NLU_MODEL", "not-a-model")

    with pytest.raises(ConfigError) as excinfo:
        load_settings(env_file=None)

    assert "LOG_LEVEL" in str(excinfo.value)
    assert "NLU_MODEL" in str(excinfo.value)


def test_secret_never_appears_in_repr_or_str(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API key is masked in every string form of the settings object."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")

    settings = load_settings(env_file=None)

    assert "sk-test-not-a-real-key" not in repr(settings)
    assert "sk-test-not-a-real-key" not in str(settings)
    assert settings.require_anthropic_key().get_secret_value() == "sk-test-not-a-real-key"


@pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "whitespace"])
def test_blank_key_counts_as_absent_and_require_fails(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """The template's empty key must not satisfy the E5 gate."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", blank)

    settings = load_settings(env_file=None)

    assert settings.anthropic_api_key is None
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        settings.require_anthropic_key()


def test_settings_are_immutable() -> None:
    """Settings cannot be mutated after validation."""
    settings = load_settings(env_file=None)

    with pytest.raises(ValueError, match="frozen"):
        settings.service_version = "changed"  # type: ignore[misc]
