"""
Application Configuration Module
================================

Overview
--------
One typed, validated configuration object for the whole service. Values come from the process
environment (and an optional ``.env`` file) and are validated completely before the service
starts, so a bad deployment fails immediately with a message naming the offending key.

Scope
-----
In: reading, validating and exposing settings; the pinned model allow-list.
Out: creating LLM clients or any other resource (composition root).

Design Principles
-----------------
- Nothing is read at import time; :func:`load_settings` is the only entry point and is called
  from the composition root.
- Secrets are ``SecretStr`` and never appear in error messages or ``repr`` output.
- Model identifiers are pinned to an allow-list so a typo or an unreviewed model cannot ship.

Runtime Contract
----------------
``load_settings(env_file=".env") -> Settings`` raises :class:`ConfigError` on any invalid value.
``Settings.require_anthropic_key() -> SecretStr`` raises :class:`ConfigError` when the key
is absent.

Limitations
-----------
The allow-list holds Anthropic API model ids; Bedrock model ids are added with the Bedrock provider
adapter.
"""

from __future__ import annotations

# Standard libraries
from enum import StrEnum  # Closed sets for environment, provider and log level
from pathlib import Path  # Type of the optional .env file location

# Third-party libraries
from pydantic import (
    Field,  # Bounds on numeric settings
    SecretStr,  # Secret values that never print
    ValidationError,  # Wrapped into ConfigError with safe messages
    field_validator,  # Model allow-list check
    model_validator,  # Rules that involve several settings
)
from pydantic_settings import (
    BaseSettings,  # Environment-backed settings
    SettingsConfigDict,  # Settings behavior (env file, extras)
)

# -----------------------------------------------------------------------------
# Constants and contracts
# -----------------------------------------------------------------------------

# Pinned model ids accepted by the service; anything else is rejected at startup. The list is
# deliberately short: the workload is structured extraction and short grounded replies, so larger
# tiers are not needed and would multiply the cost per case. Adding an id is a reviewed change.
ALLOWED_MODELS: frozenset[str] = frozenset(
    {
        "claude-haiku-4-5-20251001",
        "claude-sonnet-5",
    }
)


# Shortest accepted secrets, in characters.
MIN_SIGNING_KEY_LENGTH = 32
MIN_TEST_KEY_LENGTH = 16
MIN_DISTINCT_CHARACTERS = 8


class AppEnvironment(StrEnum):
    """Deployment environments; behavior differences live in configuration only."""

    LOCAL = "local"
    DEV = "dev"
    PROD = "prod"


class LlmProvider(StrEnum):
    """Providers selectable behind the LLM provider interface."""

    ANTHROPIC = "anthropic"
    BEDROCK = "bedrock"


class LogLevel(StrEnum):
    """Log levels accepted by the logging setup."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ConfigError(Exception):
    """Raised when configuration is invalid; the message names keys, never values."""


# -----------------------------------------------------------------------------
# Settings model
# -----------------------------------------------------------------------------


class Settings(BaseSettings):
    """Validated service configuration.

    Attributes
    ----------
    app_env : AppEnvironment
        Deployment environment.
    log_level : LogLevel
        Minimum level emitted by the logger.
    service_version : str
        Build identifier reported by the readiness endpoint.
    llm_provider : LlmProvider
        Provider behind the LLM interface.
    nlu_model, render_model : str
        Pinned model ids for understanding and rendering; must be in ``ALLOWED_MODELS``.
    anthropic_api_key : SecretStr | None
        Anthropic API key; optional until an LLM call needs it.
    session_signing_key : SecretStr | None
        Key that signs session tokens (at least 32 characters). Optional in ``local``, where the
        service generates a throw-away key at start-up; required in ``dev`` and ``prod``.
    session_ttl_seconds : int
        Lifetime of a session token, between one minute and one hour.
    test_identity_enabled : bool
        Turns on the sandbox login endpoint; never allowed in ``prod``.
    test_identity_key : SecretStr | None
        Shared secret the sandbox login endpoint requires (at least 16 characters).
    """

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    app_env: AppEnvironment = AppEnvironment.LOCAL
    log_level: LogLevel = LogLevel.INFO
    service_version: str = "dev"
    llm_provider: LlmProvider = LlmProvider.ANTHROPIC
    nlu_model: str = "claude-haiku-4-5-20251001"
    render_model: str = "claude-sonnet-5"
    anthropic_api_key: SecretStr | None = None
    session_signing_key: SecretStr | None = None
    session_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    test_identity_enabled: bool = False
    test_identity_key: SecretStr | None = None

    @field_validator("nlu_model", "render_model")
    @classmethod
    def _model_must_be_allow_listed(cls, value: str) -> str:
        """Reject any model id that is not in the reviewed allow-list."""
        if value not in ALLOWED_MODELS:
            raise ValueError("model id is not in the allow-list")
        return value

    @field_validator("anthropic_api_key")
    @classmethod
    def _blank_key_means_absent(cls, value: SecretStr | None) -> SecretStr | None:
        """Treat an empty key (as in the template) as not configured."""
        if value is not None and not value.get_secret_value().strip():
            return None
        return value

    @field_validator("session_signing_key", "test_identity_key")
    @classmethod
    def _blank_secret_means_absent(cls, value: SecretStr | None) -> SecretStr | None:
        """Treat an empty secret (as in the template) as not configured."""
        if value is not None and not value.get_secret_value().strip():
            return None
        return value

    @field_validator("session_signing_key")
    @classmethod
    def _signing_key_is_long_enough(cls, value: SecretStr | None) -> SecretStr | None:
        """A short signing key can be guessed; require at least 32 characters."""
        if value is not None and len(value.get_secret_value()) < MIN_SIGNING_KEY_LENGTH:
            raise ValueError(f"must be at least {MIN_SIGNING_KEY_LENGTH} characters")
        if value is not None and len(set(value.get_secret_value())) < MIN_DISTINCT_CHARACTERS:
            raise ValueError(
                f"must contain at least {MIN_DISTINCT_CHARACTERS} different characters"
            )
        return value

    @field_validator("test_identity_key")
    @classmethod
    def _test_key_is_long_enough(cls, value: SecretStr | None) -> SecretStr | None:
        """The sandbox login secret must not be trivially guessable either."""
        if value is not None and len(value.get_secret_value()) < MIN_TEST_KEY_LENGTH:
            raise ValueError(f"must be at least {MIN_TEST_KEY_LENGTH} characters")
        return value

    @model_validator(mode="after")
    def _test_identity_rules(self) -> Settings:
        """The sandbox login needs its key, and it can never run in production."""
        if self.test_identity_enabled and self.app_env is AppEnvironment.PROD:
            raise ValueError("TEST_IDENTITY_ENABLED is not allowed when APP_ENV=prod")
        if self.test_identity_enabled and self.test_identity_key is None:
            raise ValueError("TEST_IDENTITY_KEY is required when TEST_IDENTITY_ENABLED is true")
        return self

    def require_anthropic_key(self) -> SecretStr:
        """Return the Anthropic API key or fail with an actionable message.

        Raises
        ------
        ConfigError
            When ``ANTHROPIC_API_KEY`` is not configured.
        """
        if self.anthropic_api_key is None:
            raise ConfigError("ANTHROPIC_API_KEY is required for LLM calls but is not set")
        return self.anthropic_api_key


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Load and validate settings from the environment and an optional env file.

    Parameters
    ----------
    env_file : str | Path | None
        Path of a dotenv file; missing files are ignored. ``None`` disables file loading.

    Returns
    -------
    Settings
        Fully validated, immutable settings.

    Raises
    ------
    ConfigError
        Naming every invalid key; error text never contains configuration values.
    """
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        # Report only the key names and the validator's message, never the offending values.
        problems = sorted(
            {
                f"{'.'.join(str(part) for part in err['loc']).upper()}: {err['msg']}"
                for err in exc.errors()
            }
        )
        raise ConfigError("Invalid configuration — " + "; ".join(problems)) from None
