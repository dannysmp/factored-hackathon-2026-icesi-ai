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
    "SESSION_SIGNING_KEY",
    "SESSION_TTL_SECONDS",
    "TEST_IDENTITY_ENABLED",
    "TEST_IDENTITY_KEY",
    "DATABASE_URL",
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


def test_allow_list_holds_exactly_the_two_pinned_models() -> None:
    """The allow-list is the two pinned ids; a larger tier needs a reviewed change to it."""
    assert sorted(ALLOWED_MODELS) == ["claude-haiku-4-5-20251001", "claude-sonnet-5"]


@pytest.mark.parametrize("key", ["NLU_MODEL", "RENDER_MODEL"])
@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-fable-5-1"])
def test_larger_model_tiers_are_refused(
    monkeypatch: pytest.MonkeyPatch, key: str, model: str
) -> None:
    """Models left out of the allow-list are refused at startup for both model settings."""
    monkeypatch.setenv(key, model)

    with pytest.raises(ConfigError) as excinfo:
        load_settings(env_file=None)

    assert key in str(excinfo.value)
    assert model not in str(excinfo.value)


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


# -----------------------------------------------------------------------------
# Session and sandbox-login settings
# -----------------------------------------------------------------------------


def test_session_settings_default_to_no_key_a_quarter_hour_and_no_sandbox_login() -> None:
    """Nothing about sessions is enabled or secret-bearing by default."""
    settings = load_settings(env_file=None)

    assert settings.session_signing_key is None
    assert settings.session_ttl_seconds == 900
    assert settings.test_identity_enabled is False
    assert settings.test_identity_key is None


def test_blank_secrets_in_the_template_mean_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The committed template leaves the keys empty; that must not be a validation error."""
    monkeypatch.setenv("SESSION_SIGNING_KEY", "  ")
    monkeypatch.setenv("TEST_IDENTITY_KEY", "")

    settings = load_settings(env_file=None)

    assert settings.session_signing_key is None and settings.test_identity_key is None


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("SESSION_SIGNING_KEY", "short"),
        ("SESSION_SIGNING_KEY", "k" * 31),
        ("TEST_IDENTITY_KEY", "k" * 15),
    ],
)
def test_short_secrets_are_rejected_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    """A guessable secret stops the start-up; the message names the key, never the value."""
    monkeypatch.setenv(variable, value)

    with pytest.raises(ConfigError) as raised:
        load_settings(env_file=None)

    assert variable in str(raised.value) and value not in str(raised.value)


def test_the_shortest_accepted_secrets_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bounds are inclusive: 32 and 16 characters."""
    monkeypatch.setenv("SESSION_SIGNING_KEY", "abcdefgh" * 4)
    monkeypatch.setenv("TEST_IDENTITY_KEY", "t" * 16)

    settings = load_settings(env_file=None)

    assert settings.session_signing_key is not None and settings.test_identity_key is not None


@pytest.mark.parametrize("ttl", ["59", "3601", "0", "-1", "soon"])
def test_a_session_lifetime_outside_one_minute_to_one_hour_is_rejected(
    monkeypatch: pytest.MonkeyPatch, ttl: str
) -> None:
    """Sessions are short-lived by construction."""
    monkeypatch.setenv("SESSION_TTL_SECONDS", ttl)

    with pytest.raises(ConfigError, match="SESSION_TTL_SECONDS"):
        load_settings(env_file=None)


@pytest.mark.parametrize("ttl", ["60", "3600"])
def test_the_lifetime_bounds_are_inclusive(monkeypatch: pytest.MonkeyPatch, ttl: str) -> None:
    """One minute and one hour are valid."""
    monkeypatch.setenv("SESSION_TTL_SECONDS", ttl)

    assert load_settings(env_file=None).session_ttl_seconds == int(ttl)


def test_the_sandbox_login_needs_its_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabling it without the shared secret is a start-up error."""
    monkeypatch.setenv("TEST_IDENTITY_ENABLED", "true")

    with pytest.raises(ConfigError, match="TEST_IDENTITY_KEY is required"):
        load_settings(env_file=None)


def test_the_sandbox_login_can_never_be_enabled_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever else is set, prod refuses the sandbox login."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("TEST_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("TEST_IDENTITY_KEY", "t" * 32)

    with pytest.raises(ConfigError, match="not allowed when APP_ENV=prod"):
        load_settings(env_file=None)


def test_the_sandbox_login_can_be_enabled_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local and dev may enable it with its secret."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("TEST_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("TEST_IDENTITY_KEY", "t" * 16)

    assert load_settings(env_file=None).test_identity_enabled is True


@pytest.mark.parametrize("key", ["k" * 40, "ab" * 20, "abcdefg" * 6])
def test_a_signing_key_with_few_different_characters_is_rejected(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """Forty repeated characters are long but guessable."""
    monkeypatch.setenv("SESSION_SIGNING_KEY", key)

    with pytest.raises(ConfigError, match="different characters"):
        load_settings(env_file=None)


# -----------------------------------------------------------------------------
# Serving-store DSN
# -----------------------------------------------------------------------------


def test_database_url_defaults_to_absent() -> None:
    """The service starts without a serving store configured."""
    assert load_settings(env_file=None).database_url is None


@pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "whitespace"])
def test_blank_database_url_counts_as_absent_and_require_fails(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """The template's empty DSN must not satisfy a feature that needs the store."""
    monkeypatch.setenv("DATABASE_URL", blank)

    settings = load_settings(env_file=None)

    assert settings.database_url is None
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        settings.require_database_url()


def test_database_url_never_appears_in_repr_or_str(monkeypatch: pytest.MonkeyPatch) -> None:
    """The DSN is masked in every string form of the settings object."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:not-a-real-password@localhost/db")

    settings = load_settings(env_file=None)

    assert "not-a-real-password" not in repr(settings)
    assert "not-a-real-password" not in str(settings)
    assert (
        settings.require_database_url().get_secret_value()
        == "postgresql://user:not-a-real-password@localhost/db"
    )
