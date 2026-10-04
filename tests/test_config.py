"""
Configuration Tests
===================

Component: ``app.config``. Hermetic: environment is set through ``monkeypatch`` and no
``.env`` file is read (``env_file=None`` or a temp file).
Out of scope: how other modules consume settings.
"""

from __future__ import annotations

# Standard libraries
from decimal import Decimal  # The spend limit's type
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
    "JUDGE_MODEL",
    "ANTHROPIC_API_KEY",
    "SESSION_SIGNING_KEY",
    "SESSION_TTL_SECONDS",
    "TEST_IDENTITY_ENABLED",
    "TEST_IDENTITY_KEY",
    "DATABASE_URL",
    "MODEL_RENDERER_ENABLED",
    "CASE_CREATE_SESSION_CAP",
    "DIALOGUE_MAX_TURNS",
    "LLM_RETRY_MAX_ATTEMPTS",
    "LLM_RETRY_BASE_DELAY_MS",
    "LLM_RETRY_MAX_DELAY_MS",
    "TOOL_RETRY_MAX_ATTEMPTS",
    "TOOL_RETRY_BASE_DELAY_MS",
    "TOOL_RETRY_MAX_DELAY_MS",
    "LLM_BREAKER_FAILURE_THRESHOLD",
    "LLM_BREAKER_RESET_SECONDS",
    "TOOL_BREAKER_FAILURE_THRESHOLD",
    "TOOL_BREAKER_RESET_SECONDS",
    "POST_HANDOFF_CONTACT_DAYS_PRIORITY",
    "POST_HANDOFF_CONTACT_DAYS_DEFAULT",
    "DEMO_SIGNIN_ENABLED",
    "DEMO_SIGNIN_ACCESS_CODE",
    "DEMO_AGENT_SIGNIN_ENABLED",
    "DEMO_AGENT_ACCESS_CODE",
    "AGENT_SESSION_SIGNING_KEY",
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
    assert settings.judge_model in ALLOWED_MODELS
    assert settings.anthropic_api_key is None


def test_allow_list_holds_exactly_the_two_pinned_models() -> None:
    """The allow-list is the two pinned ids; a larger tier needs a reviewed change to it."""
    assert sorted(ALLOWED_MODELS) == ["claude-haiku-4-5-20251001", "claude-sonnet-5"]


@pytest.mark.parametrize("key", ["NLU_MODEL", "RENDER_MODEL", "JUDGE_MODEL"])
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
        ("JUDGE_MODEL", "gpt-4o"),
    ],
    ids=["environment", "log-level", "provider", "nlu-model", "render-model", "judge-model"],
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


def test_the_case_create_session_cap_defaults_to_three() -> None:
    """A bare environment yields the documented default."""
    assert load_settings(env_file=None).case_create_session_cap == 3


@pytest.mark.parametrize("cap", ["0", "51", "-1", "many"])
def test_a_case_create_session_cap_outside_one_to_fifty_is_rejected(
    monkeypatch: pytest.MonkeyPatch, cap: str
) -> None:
    """The cap is a small, positive, bounded integer."""
    monkeypatch.setenv("CASE_CREATE_SESSION_CAP", cap)

    with pytest.raises(ConfigError, match="CASE_CREATE_SESSION_CAP"):
        load_settings(env_file=None)


@pytest.mark.parametrize("cap", ["1", "50"])
def test_the_case_create_session_cap_bounds_are_inclusive(
    monkeypatch: pytest.MonkeyPatch, cap: str
) -> None:
    """One and fifty are valid."""
    monkeypatch.setenv("CASE_CREATE_SESSION_CAP", cap)

    assert load_settings(env_file=None).case_create_session_cap == int(cap)


def test_the_reliability_settings_default_to_the_designed_values() -> None:
    """A bare environment yields the documented retry and breaker defaults."""
    settings = load_settings(env_file=None)

    assert settings.llm_retry_max_attempts == 2
    assert settings.llm_retry_base_delay_ms == 200
    assert settings.llm_retry_max_delay_ms == 2000
    assert settings.tool_retry_max_attempts == 3
    assert settings.tool_retry_base_delay_ms == 50
    assert settings.tool_retry_max_delay_ms == 400
    assert settings.llm_breaker_failure_threshold == 5
    assert settings.llm_breaker_reset_seconds == 30.0
    assert settings.tool_breaker_failure_threshold == 5
    assert settings.tool_breaker_reset_seconds == 10.0


def test_the_daily_spend_limit_defaults_to_ten_dollars() -> None:
    assert load_settings(env_file=None).llm_daily_spend_limit_usd == Decimal("10")


def test_the_daily_spend_limit_is_read_from_the_environment_as_a_decimal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_DAILY_SPEND_LIMIT_USD", "2.50")

    assert load_settings(env_file=None).llm_daily_spend_limit_usd == Decimal("2.50")


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "abc", ""])
def test_a_daily_spend_limit_that_is_not_a_positive_finite_amount_is_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A zero or unparsed limit would either disable the guard or refuse every call."""
    monkeypatch.setenv("LLM_DAILY_SPEND_LIMIT_USD", value)

    with pytest.raises(ConfigError, match="LLM_DAILY_SPEND_LIMIT_USD"):
        load_settings(env_file=None)


@pytest.mark.parametrize(
    "name",
    [
        "LLM_RETRY_MAX_ATTEMPTS",
        "TOOL_RETRY_MAX_ATTEMPTS",
        "LLM_BREAKER_FAILURE_THRESHOLD",
        "TOOL_BREAKER_FAILURE_THRESHOLD",
    ],
)
def test_a_zero_attempts_or_threshold_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Zero attempts or a zero failure threshold would never actually call or never actually
    open; both are configuration mistakes, not valid bounds."""
    monkeypatch.setenv(name, "0")

    with pytest.raises(ConfigError, match=name):
        load_settings(env_file=None)


def test_the_post_handoff_contact_days_default_to_the_ratified_figures() -> None:
    """A bare environment yields the ratified figures: 1 day priority, 2 days otherwise."""
    settings = load_settings(env_file=None)

    assert settings.post_handoff_contact_days_priority == 1
    assert settings.post_handoff_contact_days_default == 2


@pytest.mark.parametrize(
    ("name", "value"),
    [("POST_HANDOFF_CONTACT_DAYS_PRIORITY", "-1"), ("POST_HANDOFF_CONTACT_DAYS_DEFAULT", "-1")],
)
def test_a_negative_post_handoff_contact_days_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigError, match=name):
        load_settings(env_file=None)


@pytest.mark.parametrize(
    "name", ["LLM_RETRY_BASE_DELAY_MS", "TOOL_RETRY_BASE_DELAY_MS", "LLM_RETRY_MAX_DELAY_MS"]
)
def test_a_negative_delay_is_rejected(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "-1")

    with pytest.raises(ConfigError, match=name):
        load_settings(env_file=None)


def test_a_zero_base_delay_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero (retry immediately, no backoff) is a valid, if aggressive, configuration."""
    monkeypatch.setenv("LLM_RETRY_BASE_DELAY_MS", "0")

    assert load_settings(env_file=None).llm_retry_base_delay_ms == 0


def test_a_zero_post_handoff_contact_days_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero (same-day) is a valid, if aggressive, configuration."""
    monkeypatch.setenv("POST_HANDOFF_CONTACT_DAYS_PRIORITY", "0")

    assert load_settings(env_file=None).post_handoff_contact_days_priority == 0


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


def test_the_stub_llm_provider_can_never_be_enabled_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever else is set, prod refuses the stub LLM provider, same reasoning as the sandbox
    login: a CI-only shortcut must never reach a real deployment."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("LLM_PROVIDER", "stub")

    with pytest.raises(ConfigError, match="not allowed when APP_ENV=prod"):
        load_settings(env_file=None)


def test_the_stub_llm_provider_can_be_enabled_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local, dev and CI may select the stub provider freely."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("LLM_PROVIDER", "stub")

    assert load_settings(env_file=None).llm_provider is LlmProvider.STUB


def test_the_demo_broker_needs_its_own_access_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabling it without the shared access code is a start-up error."""
    monkeypatch.setenv("DEMO_SIGNIN_ENABLED", "true")

    with pytest.raises(ConfigError, match="DEMO_SIGNIN_ACCESS_CODE is required"):
        load_settings(env_file=None)


def test_the_demo_broker_and_the_sandbox_login_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both are sign-in paths; enabling both at once is a configuration error, not a priority."""
    monkeypatch.setenv("DEMO_SIGNIN_ENABLED", "true")
    monkeypatch.setenv("DEMO_SIGNIN_ACCESS_CODE", "d" * 16)
    monkeypatch.setenv("TEST_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("TEST_IDENTITY_KEY", "t" * 16)

    with pytest.raises(ConfigError, match="mutually exclusive"):
        load_settings(env_file=None)


def test_the_demo_broker_can_be_enabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike the sandbox login, the demo broker exists precisely for prod (ADR-18)."""
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DEMO_SIGNIN_ENABLED", "true")
    monkeypatch.setenv("DEMO_SIGNIN_ACCESS_CODE", "d" * 16)

    assert load_settings(env_file=None).demo_signin_enabled is True


@pytest.mark.parametrize("code", ["c" * 15])
def test_a_short_demo_access_code_is_rejected(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    """The demo access code has the same length floor as the sandbox login's own key."""
    monkeypatch.setenv("DEMO_SIGNIN_ENABLED", "true")
    monkeypatch.setenv("DEMO_SIGNIN_ACCESS_CODE", code)

    with pytest.raises(ConfigError, match="at least 16 characters"):
        load_settings(env_file=None)


def test_a_blank_demo_access_code_is_treated_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty value, as the template ships it, means not configured."""
    monkeypatch.setenv("DEMO_SIGNIN_ACCESS_CODE", "")

    assert load_settings(env_file=None).demo_signin_access_code is None


@pytest.mark.parametrize("key", ["k" * 40, "ab" * 20, "abcdefg" * 6])
def test_a_signing_key_with_few_different_characters_is_rejected(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """Forty repeated characters are long but guessable."""
    monkeypatch.setenv("SESSION_SIGNING_KEY", key)

    with pytest.raises(ConfigError, match="different characters"):
        load_settings(env_file=None)


# -----------------------------------------------------------------------------
# Agent demonstration sign-in broker (ADR-17, ADR-18)
# -----------------------------------------------------------------------------


def test_the_agent_demo_broker_needs_its_own_access_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_AGENT_SIGNIN_ENABLED", "true")

    with pytest.raises(ConfigError, match="DEMO_AGENT_ACCESS_CODE is required"):
        load_settings(env_file=None)


def test_the_agent_demo_broker_and_the_sandbox_login_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEMO_AGENT_SIGNIN_ENABLED", "true")
    monkeypatch.setenv("DEMO_AGENT_ACCESS_CODE", "d" * 16)
    monkeypatch.setenv("TEST_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("TEST_IDENTITY_KEY", "t" * 16)

    with pytest.raises(ConfigError, match="mutually exclusive"):
        load_settings(env_file=None)


def test_the_customer_and_agent_demo_brokers_may_both_be_enabled_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-18 runs both together in the deployment; they are not mutually exclusive with each
    other, only each with the sandbox login."""
    monkeypatch.setenv("DEMO_SIGNIN_ENABLED", "true")
    monkeypatch.setenv("DEMO_SIGNIN_ACCESS_CODE", "c" * 16)
    monkeypatch.setenv("DEMO_AGENT_SIGNIN_ENABLED", "true")
    monkeypatch.setenv("DEMO_AGENT_ACCESS_CODE", "d" * 16)

    settings = load_settings(env_file=None)

    assert settings.demo_signin_enabled is True
    assert settings.demo_agent_signin_enabled is True


def test_the_agent_demo_broker_can_be_enabled_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DEMO_AGENT_SIGNIN_ENABLED", "true")
    monkeypatch.setenv("DEMO_AGENT_ACCESS_CODE", "d" * 16)

    assert load_settings(env_file=None).demo_agent_signin_enabled is True


@pytest.mark.parametrize("code", ["c" * 15])
def test_a_short_agent_demo_access_code_is_rejected(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    monkeypatch.setenv("DEMO_AGENT_SIGNIN_ENABLED", "true")
    monkeypatch.setenv("DEMO_AGENT_ACCESS_CODE", code)

    with pytest.raises(ConfigError, match="at least 16 characters"):
        load_settings(env_file=None)


def test_a_blank_agent_demo_access_code_is_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEMO_AGENT_ACCESS_CODE", "")

    assert load_settings(env_file=None).demo_agent_access_code is None


def test_the_agent_signing_key_has_the_same_length_floor_as_the_customer_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_SESSION_SIGNING_KEY", "k" * 31)

    with pytest.raises(ConfigError, match="at least 32 characters"):
        load_settings(env_file=None)


def test_an_agent_signing_key_with_few_different_characters_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_SESSION_SIGNING_KEY", "k" * 40)

    with pytest.raises(ConfigError, match="different characters"):
        load_settings(env_file=None)


def test_a_blank_agent_signing_key_is_treated_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_SESSION_SIGNING_KEY", "")

    assert load_settings(env_file=None).agent_session_signing_key is None


def test_the_two_access_codes_must_not_be_the_same_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A copy-paste SSM mistake must not silently defeat the two-broker separation (ADR-18)."""
    monkeypatch.setenv("DEMO_SIGNIN_ENABLED", "true")
    monkeypatch.setenv("DEMO_SIGNIN_ACCESS_CODE", "shared-code-0123456789")
    monkeypatch.setenv("DEMO_AGENT_SIGNIN_ENABLED", "true")
    monkeypatch.setenv("DEMO_AGENT_ACCESS_CODE", "shared-code-0123456789")

    with pytest.raises(ConfigError, match="must not be the same value"):
        load_settings(env_file=None)


def test_the_two_signing_keys_must_not_be_the_same_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_SIGNING_KEY", "abcdefgh" * 4)
    monkeypatch.setenv("AGENT_SESSION_SIGNING_KEY", "abcdefgh" * 4)

    with pytest.raises(ConfigError, match="must not be the same value"):
        load_settings(env_file=None)


def test_the_two_signing_keys_may_differ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_SIGNING_KEY", "abcdefgh" * 4)
    monkeypatch.setenv("AGENT_SESSION_SIGNING_KEY", "ponmlkji" * 4)

    settings = load_settings(env_file=None)

    assert settings.session_signing_key is not None
    assert settings.agent_session_signing_key is not None


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


# -----------------------------------------------------------------------------
# Model renderer disable gate
# -----------------------------------------------------------------------------


def test_model_renderer_defaults_to_disabled() -> None:
    """A bare environment never has the model renderer on."""
    assert load_settings(env_file=None).model_renderer_enabled is False


@pytest.mark.parametrize("app_env", ["local", "dev", "prod"])
def test_model_renderer_may_now_be_enabled_since_the_verifier_exists(
    monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    """The output verifier now grounds a free-form model reply, so the setting itself may be
    turned on in any environment; nothing yet calls it in a request path, so this has no effect
    until a later change wires the controller to it."""
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("MODEL_RENDERER_ENABLED", "true")

    assert load_settings(env_file=None).model_renderer_enabled is True


def test_the_dialogue_turn_cap_defaults_to_thirty() -> None:
    """A bare environment yields the documented default."""
    assert load_settings(env_file=None).dialogue_max_turns == 30


@pytest.mark.parametrize("cap", ["4", "201", "0", "-1", "many"])
def test_a_dialogue_turn_cap_outside_five_to_two_hundred_is_rejected(
    monkeypatch: pytest.MonkeyPatch, cap: str
) -> None:
    """The cap is bounded so it can neither starve a real conversation nor stop capping."""
    monkeypatch.setenv("DIALOGUE_MAX_TURNS", cap)

    with pytest.raises(ConfigError, match="DIALOGUE_MAX_TURNS"):
        load_settings(env_file=None)


@pytest.mark.parametrize("cap", ["5", "200"])
def test_the_dialogue_turn_cap_bounds_are_inclusive(
    monkeypatch: pytest.MonkeyPatch, cap: str
) -> None:
    """Five and two hundred are valid."""
    monkeypatch.setenv("DIALOGUE_MAX_TURNS", cap)

    assert load_settings(env_file=None).dialogue_max_turns == int(cap)
