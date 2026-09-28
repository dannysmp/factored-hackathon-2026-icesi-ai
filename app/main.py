"""
Service Composition Root
========================

Overview
--------
Builds the FastAPI application. Configuration is loaded here — and only here — and every
collaborator (session service, clock, attempt limiter) is created here and handed to the code
that needs it, so importing the module is free of side effects and tests can inject their own.

Scope
-----
In: the application factory, middleware order, the error handlers and the health endpoints.
Out: business routes, the tool layer and the data behind them.

Design Principles
-----------------
- ``create_app`` is a factory: ``uvicorn app.main:create_app --factory``.
- Every path under ``/v1/`` requires a session by default; only the sign-in routes that are
  actually enabled (the sandbox login, the customer demo broker, the agent demo broker) are
  public.
- Every failure leaves the service as a problem document with a stable code and the request
  identifier; stack traces and request data never reach the client.
- The service starts only with a signing key for every audience it will issue (customer always;
  agent only when its broker is enabled). In ``local`` a throw-away key is generated per audience
  (sessions end when the process restarts); in ``dev`` and ``prod`` a missing key is a start-up
  error.
- The service starts only with a resolved domain date (ADR-15): an explicit setting, the real date
  in the bank zone, or the loaded seed's own reference date; none of the three is a start-up error.
- The turns route's own heavy dependencies (a database connection, an LLM provider key) are
  resolved lazily, inside its per-request factory, never at start-up: an app that never calls
  ``/v1/turns`` — most tests, a bare health check — never needs them configured.
- Structured JSON logging (``app.observability.logging``) is installed before anything else runs,
  so every event this factory or a route logs, including a start-up failure, is already a JSON
  line carrying the service's own identity and version.
- Bounded retries and one circuit breaker per external dependency (E9) sit in front of the LLM
  client and the tool port, built once and shared across every turn the app serves — never rebuilt
  per request, since breaker state held on an object rebuilt every request could never trip.

Runtime Contract
----------------
``GET /health/live``  -> ``{"status": "live"}``
``GET /health/ready`` -> ``{"status": "ready", "service_version": str, "environment": str,
"domain_date": str, "domain_date_origin": str}`` (ADR-15: ``domain_date_origin`` is one of
``setting``, ``seed``, ``system``).
Authentication routes: see ``app.api.auth``. The turns route: see ``app.api.turns``.

Limitations
-----------
Per-turn cost and latency metrics are not implemented yet; every log line already carries a trace
id and, once authenticated, a session id (``app.observability.logging``).
"""

from __future__ import annotations

# Standard libraries
import functools  # Binds the DSN into the default customer lookup
import logging  # Structured events
import secrets  # Throw-away signing key for local runs

# Third-party libraries
from fastapi import APIRouter, FastAPI, Request  # Web framework
from fastapi.exceptions import RequestValidationError  # Validation failures of requests
from pydantic import SecretStr  # Signing key that never prints
from starlette.exceptions import HTTPException as StarletteHTTPException  # Routing failures
from starlette.responses import Response  # Handler return type

# Local modules
from app.api.auth import TEST_SESSIONS_PATH, CustomerLookup, build_auth_router  # Auth routes
from app.api.demo_signin import (  # Demo broker routes
    AGENT_SESSIONS_PATH,
    DEMO_SESSIONS_PATH,
    build_demo_agent_signin_router,
    build_demo_signin_router,
)
from app.api.turns import ControllerFactory, build_turns_router  # The turns route
from app.config import (
    AppEnvironment,  # Environments with different key rules
    ConfigError,  # Missing signing key outside local
    LlmProvider,  # Providers behind the LLM interface
    Settings,  # Validated configuration injected into the app
    load_settings,  # Loads configuration when none is injected
)
from app.conversation.controller import DialogueController, HandoffOutbox
from app.conversation.llm_understanding import LlmNlu
from app.conversation.model_renderer import LlmRenderer
from app.domain.calendar import (  # Domain date
    DomainCalendar,
    DomainCalendarError,
    resolve_domain_calendar,
)
from app.domain.policy.loader import load_policy
from app.domain.policy.models import Policy
from app.llm.anthropic_client import AnthropicLlmClient
from app.llm.client import LlmClient  # The port the retried client implements
from app.observability.logging import configure_logging  # Structured logging, installed once
from app.persistence.audit import PostgresAuditSink
from app.persistence.customers import customer_status  # The sandbox login's existence check
from app.persistence.dialogue_store import PostgresDialogueStore
from app.persistence.handoff_outbox import PostgresHandoffOutbox
from app.persistence.ops_meta import read_data_as_of  # The seed's own reference date
from app.persistence.reads import PostgresToolPort
from app.persistence.signin_audit import PostgresSignInAuditSink  # The demo broker's audit store
from app.reliability.breaker import InMemoryCircuitBreaker  # E9: shared per dependency
from app.reliability.retry import RetriedLlmClient, RetryPolicy  # E9: bounded retry
from app.reliability.tool_port import RetriedToolPort  # E9: bounded retry for the tool port
from app.retrieval.lexical import LexicalRetriever
from app.security.demo_personas import (  # The demo broker's persona list
    PersonaList,
    load_personas,
    validate_active_customers,
)
from app.security.errors import ErrorCode, ProblemError, problem_response  # Failure format
from app.security.issuance_limits import IssuanceLimiter  # Concurrent-session caps
from app.security.limits import AttemptLimiter  # Failed-login limit
from app.security.middleware import (  # Cross-cutting request handling
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SessionAuthMiddleware,
)
from app.security.sessions import (  # Sessions and the clock
    Clock,
    Principal,
    SessionService,
    utc_now,
)
from app.security.signin_audit import SignInAuditSink  # The demo broker's audit sink interface

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Composition
# -----------------------------------------------------------------------------


def _signing_key(settings: Settings) -> SecretStr:
    """The key that signs sessions.

    Raises
    ------
    ConfigError
        When no key is configured outside ``local``.
    """
    if settings.session_signing_key is not None:
        return settings.session_signing_key
    if settings.app_env is AppEnvironment.LOCAL:
        logger.warning("session_key_ephemeral sessions end when the process restarts")
        return SecretStr(secrets.token_urlsafe(48))
    raise ConfigError("SESSION_SIGNING_KEY is required when APP_ENV is dev or prod")


def _agent_signing_key(settings: Settings) -> SecretStr:
    """The key that signs agent-audience sessions; never the customer key (ADR-18).

    Raises
    ------
    ConfigError
        When no key is configured outside ``local``.
    """
    if settings.agent_session_signing_key is not None:
        return settings.agent_session_signing_key
    if settings.app_env is AppEnvironment.LOCAL:
        logger.warning("agent_session_key_ephemeral sessions end when the process restarts")
        return SecretStr(secrets.token_urlsafe(48))
    raise ConfigError("AGENT_SESSION_SIGNING_KEY is required when APP_ENV is dev or prod")


def _load_demo_state(
    settings: Settings, signin_audit: SignInAuditSink | None
) -> tuple[PersonaList, SignInAuditSink]:
    """The persona list and audit sink shared by both demo brokers, loaded once.

    ``signin_audit`` is returned unchanged when given (tests inject a fake one); otherwise the
    real, store-backed sink is built from ``DATABASE_URL``.
    """
    personas = load_personas()
    audit = signin_audit
    if audit is None:
        audit = PostgresSignInAuditSink(settings.require_database_url().get_secret_value())
    return personas, audit


def _domain_calendar(settings: Settings, *, clock: Clock) -> DomainCalendar:
    """Resolve the domain date once, at start-up (ADR-15).

    The seed is read only when ``DATA_AS_OF_DATE`` does not already settle the question, so a
    deployment that overrides it never needs the database up at start-up.

    Raises
    ------
    ConfigError
        No source resolves a domain date: neither the setting nor a loaded seed.
    """
    seed_date = None
    if not (settings.data_as_of_date or "").strip() and settings.database_url is not None:
        seed_date = read_data_as_of(settings.database_url.get_secret_value())
    try:
        return resolve_domain_calendar(settings.data_as_of_date, seed_date, now=clock)
    except DomainCalendarError as exc:
        raise ConfigError(str(exc)) from exc


def _default_customer_lookup(settings: Settings) -> CustomerLookup:
    """The sandbox login's real, store-backed customer check (AC-E4-47).

    Raises
    ------
    ConfigError
        ``DATABASE_URL`` is not configured; the sandbox login cannot check anyone without it.
    """
    dsn = settings.require_database_url().get_secret_value()
    return functools.partial(customer_status, dsn)


def _build_anthropic_client(settings: Settings) -> AnthropicLlmClient:
    """The real Anthropic adapter, built only when actually needed.

    Raises
    ------
    ConfigError
        The configured provider has no adapter yet, or its API key is not configured.
    """
    if settings.llm_provider is not LlmProvider.ANTHROPIC:
        raise ConfigError(f"the '{settings.llm_provider.value}' LLM provider has no adapter yet")
    return AnthropicLlmClient(settings.require_anthropic_key())


def _controller_factory(
    settings: Settings,
    *,
    policy: Policy,
    retriever: LexicalRetriever,
    calendar: DomainCalendar,
    clock: Clock,
) -> ControllerFactory:
    """Build the per-request factory the turns route calls with each request's own principal.

    Every dependency that needs ``DATABASE_URL`` or an LLM provider key is resolved inside the
    returned closure, not here (see the module's own Design Principles): building the factory
    itself never requires them. The LLM client and both circuit breakers (E9) are the one
    exception to "built inside the closure" — they are built once, here, and shared by every
    call the closure makes for the lifetime of this app: breaker state held on an object rebuilt
    every request would reset every request and could never trip.
    """
    llm_breaker = InMemoryCircuitBreaker(
        settings.llm_breaker_failure_threshold,
        settings.llm_breaker_reset_seconds,
        clock=clock,
        name="llm",
    )
    tool_breaker = InMemoryCircuitBreaker(
        settings.tool_breaker_failure_threshold,
        settings.tool_breaker_reset_seconds,
        clock=clock,
        name="tool",
    )
    llm_client: LlmClient = RetriedLlmClient(
        build_inner=lambda: _build_anthropic_client(settings),
        policy=RetryPolicy(
            settings.llm_retry_max_attempts,
            settings.llm_retry_base_delay_ms,
            settings.llm_retry_max_delay_ms,
        ),
        breaker=llm_breaker,
    )
    tool_retry_policy = RetryPolicy(
        settings.tool_retry_max_attempts,
        settings.tool_retry_base_delay_ms,
        settings.tool_retry_max_delay_ms,
    )

    def build(principal: Principal) -> DialogueController:
        dsn = settings.require_database_url().get_secret_value()
        store = PostgresDialogueStore(dsn)
        current = store.get(principal.session_id)
        language = current.lang if current is not None else "es"
        audit = PostgresAuditSink(dsn)
        tool_port = RetriedToolPort(
            PostgresToolPort(
                dsn,
                audit,
                policy,
                customer_id=principal.customer_id,
                session_id=principal.session_id,
                trace_id=principal.session_id,
                domain_date=calendar.reference_date,
                now=clock,
                language=language,
                case_create_session_cap=settings.case_create_session_cap,
            ),
            policy=tool_retry_policy,
            breaker=tool_breaker,
        )
        outbox: HandoffOutbox = PostgresHandoffOutbox(dsn)
        model_renderer = (
            LlmRenderer(llm_client, model=settings.render_model)
            if settings.model_renderer_enabled
            else None
        )
        return DialogueController(
            LlmNlu(llm_client, model=settings.nlu_model),
            store=store,
            tool_port=tool_port,
            retriever=retriever,
            policy=policy,
            outbox=outbox,
            domain_date=calendar.reference_date,
            now=clock,
            model_renderer=model_renderer,
        )

    return build


def _request_id(request: Request) -> str:
    """The identifier assigned to the request by the context middleware."""
    return str(getattr(request.state, "request_id", "-"))


def _validation_fields(error: RequestValidationError) -> tuple[str, ...]:
    """Names of the request parts at fault. An unknown field a client sent is shown as ``?``."""
    fields = set()
    for item in error.errors():
        parts = [str(part) for part in item["loc"]]
        if item["type"] == "extra_forbidden":
            parts[-1] = "?"
        fields.add(".".join(parts))
    return tuple(sorted(fields))


def _register_error_handlers(app: FastAPI) -> None:
    """Route every failure through the problem-document format."""

    @app.exception_handler(ProblemError)
    async def handle_problem(request: Request, error: ProblemError) -> Response:
        return problem_response(error, _request_id(request))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, error: RequestValidationError) -> Response:
        problem = ProblemError(
            ErrorCode.VALIDATION_ERROR,
            422,
            "The request is not valid",
            "Check the fields listed and try again.",
            fields=_validation_fields(error),
        )
        return problem_response(problem, _request_id(request))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, error: StarletteHTTPException) -> Response:
        known = {
            404: (ErrorCode.NOT_FOUND, "Not found"),
            405: (ErrorCode.METHOD_NOT_ALLOWED, "Method not allowed"),
        }
        code, title = known.get(error.status_code, (ErrorCode.REQUEST_REFUSED, "Request refused"))
        problem = ProblemError(code, error.status_code, title, headers=dict(error.headers or {}))
        return problem_response(problem, _request_id(request))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, error: Exception) -> Response:
        # The one place an unexpected failure is logged, with its stack trace; the client gets
        # only the code and the request identifier.
        logger.error("unhandled_error request_id=%s", _request_id(request), exc_info=error)
        problem = ProblemError(
            ErrorCode.INTERNAL_ERROR,
            500,
            "Something went wrong",
            "Quote the request identifier when you ask for help.",
        )
        return problem_response(problem, _request_id(request))


# -----------------------------------------------------------------------------
# Application factory
# -----------------------------------------------------------------------------


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock = utc_now,
    customer_lookup: CustomerLookup | None = None,
    controller_factory: ControllerFactory | None = None,
    signin_audit: SignInAuditSink | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    Parameters
    ----------
    settings : Settings | None
        Configuration to use; loaded from the environment when omitted.
    clock : Clock
        Source of the current time for sessions and the login limiters; tests inject their own.
    customer_lookup : CustomerLookup | None
        The sandbox login's existence check (AC-E4-47), reused to validate the demo broker's
        persona list against the seed (ADR-18) when it is enabled instead; tests inject a fake
        one. When omitted and either sign-in path is enabled, the real, store-backed one is built
        from ``DATABASE_URL``.
    controller_factory : ControllerFactory | None
        Builds the dialogue controller for one turn; tests inject a fake one, hermetic and without
        a real LLM provider. When omitted, the real one is built from ``DATABASE_URL`` and the
        configured LLM provider, resolved lazily on the first call (see the module's own Design
        Principles).
    signin_audit : SignInAuditSink | None
        Where either demo broker records every sign-in attempt, customer and agent alike; tests
        inject a fake one. When omitted and either broker is enabled, the real, store-backed one
        is built from ``DATABASE_URL``.

    Returns
    -------
    FastAPI
        Application with the health endpoints, the authentication routes, the authentication
        middleware and the error handlers registered.

    Raises
    ------
    ConfigError
        When no settings are given and the environment is invalid, when a signing key (customer or
        agent) is required and missing, when no domain date resolves (ADR-15), or when a sign-in
        path is enabled, no ``customer_lookup``/``signin_audit`` was injected, and
        ``DATABASE_URL`` is not configured.
    PersonaError
        Either demo broker is enabled and its persona file is missing, malformed, or names a persona
        that does not resolve to an Active seeded customer.
    """
    # Resolve configuration once, failing fast before any route is registered
    resolved = settings if settings is not None else load_settings()
    configure_logging(
        resolved.log_level,
        service_version=resolved.service_version,
        environment=resolved.app_env.value,
    )
    signing_keys = {"customer": _signing_key(resolved)}
    if resolved.demo_agent_signin_enabled:
        signing_keys["agent"] = _agent_signing_key(resolved)
    sessions = SessionService(signing_keys, resolved.session_ttl_seconds, clock=clock)
    limiter = AttemptLimiter(clock=clock)
    calendar = _domain_calendar(resolved, clock=clock)
    policy = load_policy()
    retriever = LexicalRetriever.from_corpus()
    app = FastAPI(title="Dispute Intake API", version=resolved.service_version)

    # The sandbox login is public only while it exists; otherwise its path is protected too
    test_key = resolved.test_identity_key if resolved.test_identity_enabled else None
    public_paths = [TEST_SESSIONS_PATH] if test_key is not None else []
    lookup = customer_lookup
    if (test_key is not None or resolved.demo_signin_enabled) and lookup is None:
        lookup = _default_customer_lookup(resolved)

    # Both demo brokers share one persona load and one audit sink, built at most once
    demo_router: APIRouter | None = None
    agent_demo_router: APIRouter | None = None
    if resolved.demo_signin_enabled or resolved.demo_agent_signin_enabled:
        demo_personas, demo_audit = _load_demo_state(resolved, signin_audit)
        if resolved.demo_signin_enabled:
            validate_active_customers(
                demo_personas, lookup if lookup is not None else (lambda _: None)
            )
            public_paths.append(DEMO_SESSIONS_PATH)
            demo_router = build_demo_signin_router(
                sessions=sessions,
                demo_access_code=resolved.require_demo_signin_access_code(),
                personas=demo_personas,
                audit=demo_audit,
                attempt_limiter=AttemptLimiter(clock=clock),
                issuance_limiter=IssuanceLimiter(clock=clock),
            )
        if resolved.demo_agent_signin_enabled:
            public_paths.append(AGENT_SESSIONS_PATH)
            agent_demo_router = build_demo_agent_signin_router(
                sessions=sessions,
                demo_access_code=resolved.require_demo_agent_signin_access_code(),
                personas=demo_personas,
                audit=demo_audit,
                attempt_limiter=AttemptLimiter(clock=clock),
                issuance_limiter=IssuanceLimiter(clock=clock),
            )

    # Middleware: the last one added is the outermost. The body-size cap runs after the request
    # context (so its own refusal still carries a request id) and before session authentication
    # (so an oversized body is refused before a JWT is ever verified).
    app.add_middleware(
        SessionAuthMiddleware,
        sessions=sessions,
        audience_by_prefix={"/v1": "customer"},
        public_paths=public_paths,
    )
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    _register_error_handlers(app)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        """Report that the process is up; performs no dependency checks."""
        return {"status": "live"}

    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        """Report readiness, version information and the resolved domain date (ADR-15)."""
        return {
            "status": "ready",
            "service_version": resolved.service_version,
            "environment": resolved.app_env.value,
            "domain_date": calendar.reference_date.isoformat(),
            "domain_date_origin": calendar.origin.value,
        }

    # The sandbox login is registered only when it is enabled (never in production)
    app.include_router(
        build_auth_router(
            sessions=sessions,
            test_login_key=test_key,
            limiter=limiter,
            customer_lookup=lookup if lookup is not None else (lambda customer_id: None),
        )
    )
    app.include_router(
        build_turns_router(
            controller_factory=controller_factory
            if controller_factory is not None
            else _controller_factory(
                resolved, policy=policy, retriever=retriever, calendar=calendar, clock=clock
            )
        )
    )

    # Each demo broker is registered only when it is enabled
    if demo_router is not None:
        app.include_router(demo_router)
    if agent_demo_router is not None:
        app.include_router(agent_demo_router)

    return app
