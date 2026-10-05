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
- The tool port is the same for every caller. ``create_app`` accepts one optional decorator over
  each request's tool port, outside its retry and circuit-breaker layer; nothing in production
  passes one, and an evaluation harness uses it to fail a tool for one case.
- Every path under ``/v1/`` requires a session by default; only the sign-in routes that are
  actually enabled (the sandbox login, the customer demo broker, the agent demo broker) are
  public.
- Every failure leaves the service as a problem document with a stable code and the request
  identifier; stack traces and request data never reach the client.
- The service starts only with a signing key for every audience it will issue (customer always;
  agent only when its broker is enabled). In ``local`` a throw-away key is generated per audience
  (sessions end when the process restarts); in ``dev`` and ``prod`` a missing key is a start-up
  error.
- The service starts only with a resolved domain date: an explicit setting, the real date in the
  bank zone, or the loaded seed's own reference date; none of the three is a start-up error.
- The turns route's own heavy dependencies (a database connection, an LLM provider key) are
  resolved lazily, inside its per-request factory, never at start-up: an app that never calls
  ``/v1/turns`` — most tests, a bare health check — never needs them configured.
- The console's own routes (``app.api.agent``), when the agent demo broker is enabled, are the
  opposite: their collaborators (the queue, the ticket detail, the audit sink and the writes) are
  built eagerly, here, like every other collaborator that only needs ``DATABASE_URL`` — a missing
  one is a start-up error, not a first-request surprise (``AgentConsolePorts``,
  ``_default_agent_console``).
- Configuration resolves first, and structured JSON logging installs immediately after — not
  before it, since the service version and environment logging carries come from that same
  configuration. A configuration failure itself is still captured as a JSON line, not only an
  unstructured traceback: it installs a fallback logger just for that one critical event before
  re-raising (``_load_settings_or_log_and_raise``), with placeholder identity fields
  (``service_version``/``environment`` both ``"unknown"``, since the real values were never
  resolved) rather than the service's own. Every event after configuration resolves
  successfully carries the service's real identity and version.
- Bounded retries and one circuit breaker per external dependency sit in front of the LLM
  client and the tool port, built once and shared across every turn the app serves — never rebuilt
  per request, since breaker state held on an object rebuilt every request could never trip.

Runtime Contract
----------------
``GET /health/live``  -> ``{"status": "live"}``
``GET /health/ready`` -> ``{"status": "ready", "service_version": str, "environment": str,
"domain_date": str, "domain_date_origin": str}`` (``domain_date_origin`` is one of
``setting``, ``seed``, ``system``).
Authentication routes: see ``app.api.auth``. The turns route: see ``app.api.turns``.

Limitations
-----------
Tracing is not implemented yet; every log line already carries a trace id and, once
authenticated, a session id (``app.observability.logging``), and per-turn cost and latency are
already logged from the dialogue controller (``app.conversation.controller``,
``app.observability.turn_metrics``).
"""

from __future__ import annotations

# Standard libraries
import functools  # Binds the DSN into the default customer lookup
import logging  # Structured events
import secrets  # Throw-away signing key for local runs
from dataclasses import dataclass  # The agent console's own test-injection bundle

# Third-party libraries
from fastapi import APIRouter, FastAPI, Request  # Web framework
from fastapi.exceptions import RequestValidationError  # Validation failures of requests
from pydantic import SecretStr  # Signing key that never prints
from starlette.exceptions import HTTPException as StarletteHTTPException  # Routing failures
from starlette.responses import Response  # Handler return type

# Local modules
from app.api.agent import (  # The console's own routes
    AgentWritesPort,
    ConsoleAuditSink,
    QueuePort,
    TicketDetailPort,
    build_agent_router,
)
from app.api.auth import TEST_SESSIONS_PATH, CustomerLookup, build_auth_router  # Auth routes
from app.api.demo_signin import (  # Demo broker routes
    AGENT_SESSIONS_PATH,
    DEMO_PERSONAS_PATH,
    DEMO_SESSIONS_PATH,
    build_demo_agent_signin_router,
    build_demo_persona_directory_router,
    build_demo_signin_router,
)
from app.api.turns import (  # The turns route and the hook a harness decorates its tools with
    ControllerFactory,
    ToolPortDecorator,
    build_turns_router,
)
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
from app.conversation.understanding import FakeNlu, Understanding
from app.domain.calendar import (  # Domain date
    DomainCalendar,
    DomainCalendarError,
    resolve_domain_calendar,
)
from app.domain.policy.loader import load_policy
from app.domain.policy.models import Policy
from app.llm.anthropic_client import AnthropicLlmClient
from app.llm.client import LlmClient  # The port the retried client implements
from app.llm.spend import SpendGatedLlmClient  # The daily spend breaker
from app.observability.logging import (  # Structured logging, installed once
    configure_logging,
    configure_logging_from_settings,
)
from app.persistence.agent_writes import PostgresAgentWrites  # The console's own narrow writes
from app.persistence.audit import PostgresAuditSink
from app.persistence.console_audit import PostgresConsoleAuditSink  # The console's own audit write
from app.persistence.customers import customer_status  # The sandbox login's existence check
from app.persistence.dialogue_store import PostgresDialogueStore
from app.persistence.dialogue_turn_log import PostgresDialogueTurnLog
from app.persistence.handoff_outbox import PostgresHandoffOutbox
from app.persistence.handoff_queue import PostgresHandoffQueue
from app.persistence.ops_meta import read_data_as_of  # The seed's own reference date
from app.persistence.reads import PostgresToolPort
from app.persistence.signin_audit import PostgresSignInAuditSink  # The demo broker's audit store
from app.persistence.spend_ledger import PostgresSpendLedger  # The day's recorded spend
from app.persistence.ticket_detail import PostgresTicketDetail
from app.reliability.breaker import InMemoryCircuitBreaker  # Shared per dependency
from app.reliability.retry import RetriedLlmClient, RetryPolicy  # Bounded retry
from app.reliability.tool_port import RetriedToolPort  # Bounded retry for the tool port
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
from contracts.service_v1.tools import ToolPort  # What a tool port decorator returns

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Composition
# -----------------------------------------------------------------------------


def _load_settings_or_log_and_raise() -> Settings:
    """Load settings, emitting a structured event first if that fails.

    Logging is not installed yet at this point in the factory, so a load failure would otherwise
    surface as an unstructured traceback with no queryable event; a fallback logger is installed
    just for this one critical line.

    Raises
    ------
    ConfigError
        Naming every invalid key, never its value; re-raised after the event is logged.
    """
    try:
        return load_settings()
    except ConfigError as exc:
        configure_logging_from_settings(None)
        logger.critical("config_invalid detail=%s", exc)
        raise


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
    """The key that signs agent-audience sessions; never the customer key.

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
    """Resolve the domain date once, at start-up.

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


@dataclass(frozen=True, slots=True)
class AgentConsolePorts:
    """The four collaborators the console's own routes read and write through
    (``app.api.agent``), bundled so a test can inject one hermetic value instead of four, the
    same way ``controller_factory`` and ``signin_audit`` are already injectable."""

    queue: QueuePort
    ticket_detail: TicketDetailPort
    audit: ConsoleAuditSink
    writes: AgentWritesPort


def _default_customer_lookup(settings: Settings) -> CustomerLookup:
    """The sandbox login's real, store-backed customer check.

    Raises
    ------
    ConfigError
        ``DATABASE_URL`` is not configured; the sandbox login cannot check anyone without it.
    """
    dsn = settings.require_database_url().get_secret_value()
    return functools.partial(customer_status, dsn)


def _default_agent_console(
    settings: Settings, *, calendar: DomainCalendar, retriever: LexicalRetriever, clock: Clock
) -> AgentConsolePorts:
    """The console's real, store-backed queue, ticket-detail, audit and writes collaborators.

    Raises
    ------
    ConfigError
        ``DATABASE_URL`` is not configured; the console has nothing to read from without it.
    """
    dsn = settings.require_database_url().get_secret_value()
    audit_sink = PostgresAuditSink(dsn)
    queue = PostgresHandoffQueue(
        dsn,
        contact_days_priority=settings.post_handoff_contact_days_priority,
        contact_days_default=settings.post_handoff_contact_days_default,
    )
    ticket_detail = PostgresTicketDetail(
        dsn, retriever=retriever, queue=queue, turn_log=PostgresDialogueTurnLog(dsn)
    )
    audit = PostgresConsoleAuditSink(dsn, sink=audit_sink, calendar=calendar, clock=clock)
    writes = PostgresAgentWrites(dsn, sink=audit_sink, queue=queue, calendar=calendar, clock=clock)
    return AgentConsolePorts(queue=queue, ticket_detail=ticket_detail, audit=audit, writes=writes)


def _build_agent_router(
    settings: Settings,
    agent_console: AgentConsolePorts | None,
    *,
    calendar: DomainCalendar,
    retriever: LexicalRetriever,
    clock: Clock,
) -> APIRouter:
    """The console's own routes, built on ``agent_console`` or, when omitted, the real,
    store-backed collaborators (see ``_default_agent_console``)."""
    ports = (
        agent_console
        if agent_console is not None
        else _default_agent_console(settings, calendar=calendar, retriever=retriever, clock=clock)
    )
    return build_agent_router(
        queue=ports.queue,
        ticket_detail=ports.ticket_detail,
        calendar=calendar,
        audit=ports.audit,
        writes=ports.writes,
    )


def _build_anthropic_client(settings: Settings) -> AnthropicLlmClient:
    """The real Anthropic adapter, built only when actually needed.

    Raises
    ------
    ConfigError
        The configured provider is not ``anthropic``, or its API key is not configured.
    """
    if settings.llm_provider is not LlmProvider.ANTHROPIC:
        raise ConfigError(f"the '{settings.llm_provider.value}' LLM provider has no adapter yet")
    return AnthropicLlmClient(settings.require_anthropic_key())


def _understanding(llm_client: LlmClient, settings: Settings) -> Understanding:
    """The understanding port for one turn: model-backed, or the stub CI selects.

    ``llm_client`` is the shared, retried and circuit-broken client ``_controller_factory``
    builds once — the stub branch never touches it, and the model-backed branch reuses it
    rather than building a second, unprotected client, so understanding gets the same
    resilience as every other LLM call.

    Raises
    ------
    ConfigError
        The configured provider has no adapter yet.
    """
    if settings.llm_provider is LlmProvider.STUB:
        return FakeNlu()
    if settings.llm_provider is not LlmProvider.ANTHROPIC:
        raise ConfigError(f"the '{settings.llm_provider.value}' LLM provider has no adapter yet")
    return LlmNlu(llm_client, model=settings.nlu_model)


def _model_renderer(llm_client: LlmClient, settings: Settings) -> LlmRenderer:
    """The model-backed reply renderer for one turn; only built when the feature is enabled.

    Checked eagerly, before the renderer is ever constructed: ``llm_client`` is built lazily
    (``RetriedLlmClient`` only calls ``_build_anthropic_client`` on first real use), so
    without this check a stub provider with rendering enabled would not fail until the first
    actual model call instead of failing closed up front.

    Raises
    ------
    ConfigError
        The configured provider has no adapter yet.
    """
    if settings.llm_provider is not LlmProvider.ANTHROPIC:
        raise ConfigError(f"the '{settings.llm_provider.value}' LLM provider has no adapter yet")
    return LlmRenderer(llm_client, model=settings.render_model)


def _controller_factory(
    settings: Settings,
    *,
    policy: Policy,
    retriever: LexicalRetriever,
    calendar: DomainCalendar,
    clock: Clock,
    tool_port_decorator: ToolPortDecorator | None = None,
) -> ControllerFactory:
    """Build the per-request factory the turns route calls with each request's own principal.

    Every dependency that needs ``DATABASE_URL`` or an LLM provider key is resolved inside the
    returned closure, not here (see the module's own Design Principles): building the factory
    itself never requires them. The LLM client and both circuit breakers are the one
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
        """Assemble a controller for one principal; its tool port is scoped to that customer.

        The dialogue store and the audit sink are opened per request and carry no customer scope.
        """
        dsn = settings.require_database_url().get_secret_value()
        store = PostgresDialogueStore(dsn)
        current = store.get(principal.session_id)
        language = current.lang if current is not None else "es"
        audit = PostgresAuditSink(dsn)
        store_port = PostgresToolPort(
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
        )
        tool_port: ToolPort = RetriedToolPort(
            store_port,
            policy=tool_retry_policy,
            breaker=tool_breaker,
        )
        if tool_port_decorator is not None:
            tool_port = tool_port_decorator(principal, tool_port)
        outbox: HandoffOutbox = PostgresHandoffOutbox(dsn)
        gated_client: LlmClient = SpendGatedLlmClient(
            llm_client,
            PostgresSpendLedger(dsn),
            settings.llm_daily_spend_limit_usd,
            clock,
        )
        model_renderer = (
            _model_renderer(gated_client, settings) if settings.model_renderer_enabled else None
        )
        return DialogueController(
            _understanding(gated_client, settings),
            store=store,
            tool_port=tool_port,
            retriever=retriever,
            policy=policy,
            outbox=outbox,
            domain_date=calendar.reference_date,
            now=clock,
            max_turns=settings.dialogue_max_turns,
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
        """Render a deliberately raised problem as its own document."""
        return problem_response(error, _request_id(request))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, error: RequestValidationError) -> Response:
        """Answer a malformed request with a 422 that lists the offending fields only."""
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
        """Map framework HTTP errors onto the stable error codes, keeping the status they carry."""
        known = {
            404: (ErrorCode.NOT_FOUND, "Not found"),
            405: (ErrorCode.METHOD_NOT_ALLOWED, "Method not allowed"),
        }
        code, title = known.get(error.status_code, (ErrorCode.REQUEST_REFUSED, "Request refused"))
        problem = ProblemError(code, error.status_code, title, headers=dict(error.headers or {}))
        return problem_response(problem, _request_id(request))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, error: Exception) -> Response:
        """Answer any uncaught exception with a generic 500 that exposes no internal detail."""
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
    agent_console: AgentConsolePorts | None = None,
    tool_port_decorator: ToolPortDecorator | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    Parameters
    ----------
    settings : Settings | None
        Configuration to use; loaded from the environment when omitted.
    clock : Clock
        Source of the current time for sessions and the login limiters; tests inject their own.
    customer_lookup : CustomerLookup | None
        The sandbox login's existence check, reused to validate the demo broker's
        persona list against the seed when it is enabled instead; tests inject a fake
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
    agent_console : AgentConsolePorts | None
        The console's own queue, ticket-detail, audit and writes collaborators; tests inject a
        hermetic bundle. When omitted and the agent demo broker is enabled, the real, store-backed
        ones are built from ``DATABASE_URL``.
    tool_port_decorator : ToolPortDecorator | None
        Wraps each request's tool port, outside the retry and circuit-breaker layer, so a harness
        can fail a tool without leaving breaker state behind for the next request; ignored when
        ``controller_factory`` is injected. When omitted, the tool port is used unchanged.

    Returns
    -------
    FastAPI
        Application with the health endpoints, the authentication routes, the authentication
        middleware and the error handlers registered.

    Raises
    ------
    ConfigError
        When no settings are given and the environment is invalid, when a signing key (customer or
        agent) is required and missing, when no domain date resolves, or when a sign-in
        path is enabled, no ``customer_lookup``/``signin_audit`` was injected, and
        ``DATABASE_URL`` is not configured.
    PersonaError
        Either demo broker is enabled and its persona file is missing, malformed, or names a persona
        that does not resolve to an Active seeded customer.
    """
    # Resolve configuration once, failing fast before any route is registered.
    resolved = settings if settings is not None else _load_settings_or_log_and_raise()
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
    demo_persona_router: APIRouter | None = None
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
        # Kill-switch parity applied per flag, not as one coarse on/off: a deployment could run
        # only one of the two brokers, and the directory must never leak the other audience's
        # personas while its own switch is off. Its own AttemptLimiter instance, never either
        # broker's own: those count wrong access codes, a different contract from a plain
        # per-address request cap on a route with no notion of "wrong".
        public_paths.append(DEMO_PERSONAS_PATH)
        demo_persona_router = build_demo_persona_directory_router(
            personas=demo_personas,
            include_customers=resolved.demo_signin_enabled,
            include_agents=resolved.demo_agent_signin_enabled,
            attempt_limiter=AttemptLimiter(clock=clock),
            reference_date=calendar.reference_date,
        )

    # The console's own routes, gated on the same flag as the only broker that can ever mint an
    # agent token — a second flag would gate the same precondition twice with no scenario where
    # they should disagree.
    agent_router = (
        _build_agent_router(
            resolved, agent_console, calendar=calendar, retriever=retriever, clock=clock
        )
        if resolved.demo_agent_signin_enabled
        else None
    )

    # Middleware: the last one added is the outermost. The body-size cap runs after the request
    # context (so its own refusal still carries a request id) and before session authentication
    # (so an oversized body is refused before a JWT is ever verified).
    app.add_middleware(
        SessionAuthMiddleware,
        sessions=sessions,
        # Longest-matching-prefix: "/v1/agent" is unreachable when no agent token can
        # ever be issued (the broker's own flag is off), so listing it here unconditionally costs
        # nothing and keeps this map's shape independent of which brokers happen to be enabled.
        audience_by_prefix={"/v1": "customer", "/v1/agent": "agent"},
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
        """Report readiness, version information and the resolved domain date."""
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
                resolved,
                policy=policy,
                retriever=retriever,
                calendar=calendar,
                clock=clock,
                tool_port_decorator=tool_port_decorator,
            )
        )
    )

    # Each demo broker is registered only when it is enabled
    if demo_router is not None:
        app.include_router(demo_router)
    if agent_demo_router is not None:
        app.include_router(agent_demo_router)
    if demo_persona_router is not None:
        app.include_router(demo_persona_router)
    if agent_router is not None:
        app.include_router(agent_router)

    return app
