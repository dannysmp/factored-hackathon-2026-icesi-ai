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
- Every path under ``/v1/`` requires a session by default; only the sandbox login is public.
- Every failure leaves the service as a problem document with a stable code and the request
  identifier; stack traces and request data never reach the client.
- The service starts only with a signing key. In ``local`` a throw-away key is generated (sessions
  end when the process restarts); in ``dev`` and ``prod`` a missing key is a start-up error.
- The service starts only with a resolved domain date (ADR-15): an explicit setting, the real date
  in the bank zone, or the loaded seed's own reference date; none of the three is a start-up error.

Runtime Contract
----------------
``GET /health/live``  -> ``{"status": "live"}``
``GET /health/ready`` -> ``{"status": "ready", "service_version": str, "environment": str,
"domain_date": str, "domain_date_origin": str}`` (ADR-15: ``domain_date_origin`` is one of
``setting``, ``seed``, ``system``).
Authentication routes: see ``app.api.auth``.

Limitations
-----------
Request logging, tracing and metrics are not implemented yet; the security events are logged with
the request identifier.
"""

from __future__ import annotations

# Standard libraries
import functools  # Binds the DSN into the default customer lookup
import logging  # Structured events
import secrets  # Throw-away signing key for local runs

# Third-party libraries
from fastapi import FastAPI, Request  # Web framework
from fastapi.exceptions import RequestValidationError  # Validation failures of requests
from pydantic import SecretStr  # Signing key that never prints
from starlette.exceptions import HTTPException as StarletteHTTPException  # Routing failures
from starlette.responses import Response  # Handler return type

# Local modules
from app.api.auth import TEST_SESSIONS_PATH, CustomerLookup, build_auth_router  # Auth routes
from app.config import (
    AppEnvironment,  # Environments with different key rules
    ConfigError,  # Missing signing key outside local
    Settings,  # Validated configuration injected into the app
    load_settings,  # Loads configuration when none is injected
)
from app.domain.calendar import (  # Domain date
    DomainCalendar,
    DomainCalendarError,
    resolve_domain_calendar,
)
from app.persistence.customers import customer_status  # The sandbox login's existence check
from app.persistence.ops_meta import read_data_as_of  # The seed's own reference date
from app.security.errors import ErrorCode, ProblemError, problem_response  # Failure format
from app.security.limits import AttemptLimiter  # Failed-login limit
from app.security.middleware import (  # Cross-cutting request handling
    RequestContextMiddleware,
    SessionAuthMiddleware,
)
from app.security.sessions import Clock, SessionService, utc_now  # Sessions and the clock

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
) -> FastAPI:
    """Build the FastAPI application.

    Parameters
    ----------
    settings : Settings | None
        Configuration to use; loaded from the environment when omitted.
    clock : Clock
        Source of the current time for sessions and the login limiter; tests inject their own.
    customer_lookup : CustomerLookup | None
        The sandbox login's existence check (AC-E4-47); tests inject a fake one. When omitted and
        the sandbox login is enabled, the real, store-backed one is built from ``DATABASE_URL``.

    Returns
    -------
    FastAPI
        Application with the health endpoints, the authentication routes, the authentication
        middleware and the error handlers registered.

    Raises
    ------
    ConfigError
        When no settings are given and the environment is invalid, when a signing key is required
        and missing, when no domain date resolves (ADR-15), or when the sandbox login is enabled,
        no ``customer_lookup`` was injected, and ``DATABASE_URL`` is not configured.
    """
    # Resolve configuration once, failing fast before any route is registered
    resolved = settings if settings is not None else load_settings()
    sessions = SessionService(_signing_key(resolved), resolved.session_ttl_seconds, clock=clock)
    limiter = AttemptLimiter(clock=clock)
    calendar = _domain_calendar(resolved, clock=clock)
    app = FastAPI(title="Dispute Intake API", version=resolved.service_version)

    # The sandbox login is public only while it exists; otherwise its path is protected too
    test_key = resolved.test_identity_key if resolved.test_identity_enabled else None
    public_paths = (TEST_SESSIONS_PATH,) if test_key is not None else ()
    lookup = customer_lookup
    if test_key is not None and lookup is None:
        lookup = _default_customer_lookup(resolved)

    # Middleware: the last one added is the outermost, so the request context wraps the rest
    app.add_middleware(SessionAuthMiddleware, sessions=sessions, public_paths=public_paths)
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

    return app
