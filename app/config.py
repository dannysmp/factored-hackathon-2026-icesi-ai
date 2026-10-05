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
- Free-form model rendering is wired into the request path (the dialogue controller builds a
  model candidate and slot values for every eligible template, ``app.conversation.reply``) and is
  grounded against the envelope's own facts by the output verifier (``app.conversation.verifier``);
  the setting still defaults to off.

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
from decimal import Decimal  # The daily spend limit is money, never a float
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
    """Providers selectable behind the LLM provider interface.

    ``STUB`` makes no model call at all: the evaluation harness's smoke subset selects it so CI
    exercises the real turns endpoint and dialogue controller without a network call or a
    configured API key. It is never available in production (see ``Settings._stub_llm_rules``).
    """

    ANTHROPIC = "anthropic"
    BEDROCK = "bedrock"
    STUB = "stub"


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
    nlu_model, render_model, judge_model : str
        Pinned model ids for understanding, rendering and the evaluation harness's LLM judge;
        must be in ``ALLOWED_MODELS``. ``judge_model`` is read only by the evaluation harness,
        never by a request path this service serves to a customer.
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
    database_url : SecretStr | None
        DSN of the serving store; optional until a feature that reads or writes it runs.
    model_renderer_enabled : bool
        Whether free-form model rendering runs. Defaults to ``False``. When ``True``, the dialogue
        controller builds a model candidate and slot values for every eligible template
        (``app.conversation.reply.MODEL_ELIGIBLE_TEMPLATES``) on every request, grounded against
        the envelope's own facts by the output verifier (``app.conversation.verifier``); building
        the model renderer itself still requires ``llm_provider`` to be ``anthropic``
        (``_model_renderer`` in ``app.main``), so enabling this with any other provider fails
        closed with a ``ConfigError`` on every request, never a silent no-op and never a call to
        that other provider.
    data_as_of_date : str | None
        The domain date override: an ISO date, or the literal ``"system"`` for the real date in
        the bank's own zone. Optional; when absent, the domain calendar reads the loaded seed's own
        reference date instead.
    case_create_session_cap : int
        Permission-class invariant the create tool enforces itself, not a policy value:
        an anti-abuse bound on how many cases one session may file, never a limit on how many
        distinct disputes a customer legitimately has. Between 1 and 50.
    dialogue_max_turns : int
        The most customer turns one session may apply before the next is answered with a handoff
        to a person and no model call. A cost control, not a policy value: it bounds the turns one
        session applies, and so the model calls it can cause after that point. Between 5 and 200;
        the default sits well above the longest normal flow.
    llm_retry_max_attempts, llm_retry_base_delay_ms, llm_retry_max_delay_ms : int
        Bounded retry for a transient LLM failure (``LlmUnavailable``): full-jitter
        exponential backoff between attempts, capped at ``llm_retry_max_delay_ms``. A permanent
        failure (``LlmRequestRejected``, ``LlmOutputInvalid``) is never retried, whatever these
        values are.
    tool_retry_max_attempts, tool_retry_base_delay_ms, tool_retry_max_delay_ms : int
        Bounded retry for a retryable tool failure (``ToolFailure.retryable`` and
        ``cause`` in ``"timeout"``/``"error"``, never ``"circuit_open"``); a refusal for a
        permission or not-found reason (``retryable=False``) is never retried.
    llm_daily_spend_limit_usd : Decimal
        The daily model spend limit, counted per operating day: once the day's recorded
        spend reaches it, model calls are refused and every turn degrades to a template reply and
        a handoff to a person until the next operating day.
    llm_breaker_failure_threshold, llm_breaker_reset_seconds : int, float
        The LLM circuit breaker: opens after this many consecutive post-retry failures, and
        allows one trial call again after this many seconds.
    tool_breaker_failure_threshold, tool_breaker_reset_seconds : int, float
        The tool-port circuit breaker, shared by every tool method: the store is one
        dependency, not six, so one outage trips one breaker.
    post_handoff_contact_days_priority, post_handoff_contact_days_default : int
        The promised contact time after a handoff: 1 calendar day for a fraud report or a lost
        or stolen card, 2 calendar days for every other trigger, counted
        from the data reference date. A synthetic configuration value with its own provenance,
        separate from ``Policy.first_response_days`` (which promises a response to a *filed
        dispute*, a different lifecycle event a handoff ticket never reaches) and keyed by the
        handoff's own trigger, never its dispute category.
    demo_signin_enabled : bool
        Turns on the demonstration sign-in broker for customers: a public, persona-based
        sign-in path meant for the deployed demonstration, unlike the sandbox login. Mutually
        exclusive with ``test_identity_enabled``; unlike it, not restricted to any environment.
    demo_signin_access_code : SecretStr | None
        Shared secret the demo sign-in broker requires (at least 16 characters), compared in
        constant time and rate-limited.
    demo_agent_signin_enabled : bool
        Turns on the demonstration sign-in broker for agents: the console's own
        sign-in path, separate from the customer broker so a leaked customer code never exposes
        it. Mutually exclusive with ``test_identity_enabled``; not restricted to any environment.
    demo_agent_access_code : SecretStr | None
        Shared secret the agent demo sign-in broker requires (at least 16 characters), its own
        code so it never shares a failure domain with the customer broker's.
    agent_session_signing_key : SecretStr | None
        Key that signs agent-audience session tokens (at least 32 characters), separate from
        ``session_signing_key`` so a customer token and an agent token can never be confused even
        if one key were compromised. Optional in ``local``, where a throw-away key is
        generated; required in ``dev`` and ``prod`` once the agent broker is enabled.
    """

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    app_env: AppEnvironment = AppEnvironment.LOCAL
    log_level: LogLevel = LogLevel.INFO
    service_version: str = "dev"
    llm_provider: LlmProvider = LlmProvider.ANTHROPIC
    nlu_model: str = "claude-haiku-4-5-20251001"
    render_model: str = "claude-sonnet-5"
    judge_model: str = "claude-sonnet-5"
    anthropic_api_key: SecretStr | None = None
    session_signing_key: SecretStr | None = None
    session_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    test_identity_enabled: bool = False
    test_identity_key: SecretStr | None = None
    database_url: SecretStr | None = None
    model_renderer_enabled: bool = False
    data_as_of_date: str | None = None
    case_create_session_cap: int = Field(default=3, ge=1, le=50)
    dialogue_max_turns: int = Field(default=30, ge=5, le=200)
    llm_retry_max_attempts: int = Field(default=2, ge=1, le=5)
    llm_retry_base_delay_ms: int = Field(default=200, ge=0, le=5000)
    llm_retry_max_delay_ms: int = Field(default=2000, ge=0, le=30000)
    tool_retry_max_attempts: int = Field(default=3, ge=1, le=5)
    tool_retry_base_delay_ms: int = Field(default=50, ge=0, le=5000)
    tool_retry_max_delay_ms: int = Field(default=400, ge=0, le=30000)
    llm_daily_spend_limit_usd: Decimal = Field(default=Decimal("10"), gt=0)
    llm_breaker_failure_threshold: int = Field(default=5, ge=1, le=20)
    llm_breaker_reset_seconds: float = Field(default=30.0, ge=1, le=300)
    tool_breaker_failure_threshold: int = Field(default=5, ge=1, le=20)
    tool_breaker_reset_seconds: float = Field(default=10.0, ge=1, le=300)
    post_handoff_contact_days_priority: int = Field(default=1, ge=0)
    post_handoff_contact_days_default: int = Field(default=2, ge=0)
    demo_signin_enabled: bool = False
    demo_signin_access_code: SecretStr | None = None
    demo_agent_signin_enabled: bool = False
    demo_agent_access_code: SecretStr | None = None
    agent_session_signing_key: SecretStr | None = None

    @field_validator("nlu_model", "render_model", "judge_model")
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

    @field_validator(
        "session_signing_key",
        "test_identity_key",
        "database_url",
        "demo_signin_access_code",
        "demo_agent_access_code",
        "agent_session_signing_key",
    )
    @classmethod
    def _blank_secret_means_absent(cls, value: SecretStr | None) -> SecretStr | None:
        """Treat an empty secret (as in the template) as not configured."""
        if value is not None and not value.get_secret_value().strip():
            return None
        return value

    @field_validator("session_signing_key", "agent_session_signing_key")
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

    @field_validator("test_identity_key", "demo_signin_access_code", "demo_agent_access_code")
    @classmethod
    def _test_key_is_long_enough(cls, value: SecretStr | None) -> SecretStr | None:
        """The sandbox login secret and the demo access codes must not be trivially guessable."""
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

    @model_validator(mode="after")
    def _stub_llm_rules(self) -> Settings:
        """The stub LLM provider exists only to keep CI network-free; it can never run in
        production, the same restriction the sandbox login carries and for the same reason."""
        if self.llm_provider is LlmProvider.STUB and self.app_env is AppEnvironment.PROD:
            raise ValueError("LLM_PROVIDER=stub is not allowed when APP_ENV=prod")
        return self

    @model_validator(mode="after")
    def _demo_signin_rules(self) -> Settings:
        """The demo broker needs its own code and never runs alongside the sandbox login.

        Unlike the sandbox login, the demo broker is not restricted to any environment: it exists
        precisely so the deployed demonstration (which runs as ``prod``) has a working sign-in.
        """
        if self.demo_signin_enabled and self.test_identity_enabled:
            raise ValueError("DEMO_SIGNIN_ENABLED and TEST_IDENTITY_ENABLED are mutually exclusive")
        if self.demo_signin_enabled and self.demo_signin_access_code is None:
            raise ValueError("DEMO_SIGNIN_ACCESS_CODE is required when DEMO_SIGNIN_ENABLED is true")
        return self

    @model_validator(mode="after")
    def _demo_agent_signin_rules(self) -> Settings:
        """The agent demo broker needs its own code and never runs alongside the sandbox login.

        A separate rule from the customer broker's own (rather than one combined check) because
        the two settings are independent: either, both or neither may be on (the deployment runs
        both together), and each names its own missing setting in the error.
        """
        if self.demo_agent_signin_enabled and self.test_identity_enabled:
            raise ValueError(
                "DEMO_AGENT_SIGNIN_ENABLED and TEST_IDENTITY_ENABLED are mutually exclusive"
            )
        if self.demo_agent_signin_enabled and self.demo_agent_access_code is None:
            raise ValueError(
                "DEMO_AGENT_ACCESS_CODE is required when DEMO_AGENT_SIGNIN_ENABLED is true"
            )
        return self

    @model_validator(mode="after")
    def _demo_broker_secrets_never_collide(self) -> Settings:
        """A copy-paste SSM mistake must not silently defeat the two-broker separation.

        Two access codes exist so that a leaked customer code leaves the console protected; the same
        reasoning applies to the two signing keys. Checked only when both values are actually
        configured, so one broker alone never trips this.
        """
        if (
            self.demo_signin_access_code is not None
            and self.demo_agent_access_code is not None
            and self.demo_signin_access_code.get_secret_value()
            == self.demo_agent_access_code.get_secret_value()
        ):
            raise ValueError(
                "DEMO_SIGNIN_ACCESS_CODE and DEMO_AGENT_ACCESS_CODE must not be the same value"
            )
        if (
            self.session_signing_key is not None
            and self.agent_session_signing_key is not None
            and self.session_signing_key.get_secret_value()
            == self.agent_session_signing_key.get_secret_value()
        ):
            raise ValueError(
                "SESSION_SIGNING_KEY and AGENT_SESSION_SIGNING_KEY must not be the same value"
            )
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

    def require_demo_signin_access_code(self) -> SecretStr:
        """Return the demo broker's access code or fail with an actionable message.

        Raises
        ------
        ConfigError
            When ``DEMO_SIGNIN_ACCESS_CODE`` is not configured.
        """
        if self.demo_signin_access_code is None:
            raise ConfigError(
                "DEMO_SIGNIN_ACCESS_CODE is required when DEMO_SIGNIN_ENABLED is true"
            )
        return self.demo_signin_access_code

    def require_demo_agent_signin_access_code(self) -> SecretStr:
        """Return the agent demo broker's access code or fail with an actionable message.

        Raises
        ------
        ConfigError
            When ``DEMO_AGENT_ACCESS_CODE`` is not configured.
        """
        if self.demo_agent_access_code is None:
            raise ConfigError(
                "DEMO_AGENT_ACCESS_CODE is required when DEMO_AGENT_SIGNIN_ENABLED is true"
            )
        return self.demo_agent_access_code

    def require_database_url(self) -> SecretStr:
        """Return the serving-store DSN or fail with an actionable message.

        Raises
        ------
        ConfigError
            When ``DATABASE_URL`` is not configured.
        """
        if self.database_url is None:
            raise ConfigError("DATABASE_URL is required for this operation but is not set")
        return self.database_url


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
