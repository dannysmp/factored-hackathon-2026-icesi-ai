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

Runtime Contract
----------------
``GET /health/live``  -> ``{"status": "live"}``
``GET /health/ready`` -> ``{"status": "ready", "service_version": str, "environment": str}``
Authentication routes: see ``app.api.auth``.

Limitations
-----------
Request logging, tracing and metrics are not implemented yet; the security events are logged with
the request identifier.
"""

from __future__ import annotations

# Standard libraries
import logging  # Structured events
import secrets  # Throw-away signing key for local runs

# Third-party libraries
from fastapi import FastAPI, Request  # Web framework
from fastapi.exceptions import RequestValidationError  # Validation failures of requests
from pydantic import SecretStr  # Signing key that never prints
from starlette.exceptions import HTTPException as StarletteHTTPException  # Routing failures
from starlette.responses import Response  # Handler return type

# Local modules
from app.api.auth import TEST_SESSIONS_PATH, build_auth_router  # Authentication routes
from app.config import (
    AppEnvironment,  # Environments with different key rules
    ConfigError,  # Missing signing key outside local
    Settings,  # Validated configuration injected into the app
    load_settings,  # Loads configuration when none is injected
)
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
        code, title = known.get(error.status_code, (ErrorCode.VALIDATION_ERROR, "Request refused"))
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


def create_app(settings: Settings | None = None, *, clock: Clock = utc_now) -> FastAPI:
    """Build the FastAPI application.

    Parameters
    ----------
    settings : Settings | None
        Configuration to use; loaded from the environment when omitted.
    clock : Clock
        Source of the current time for sessions and the login limiter; tests inject their own.

    Returns
    -------
    FastAPI
        Application with the health endpoints, the authentication routes, the authentication
        middleware and the error handlers registered.

    Raises
    ------
    ConfigError
        When no settings are given and the environment is invalid, or when a signing key is
        required and missing.
    """
    # Resolve configuration once, failing fast before any route is registered
    resolved = settings if settings is not None else load_settings()
    sessions = SessionService(_signing_key(resolved), resolved.session_ttl_seconds, clock=clock)
    limiter = AttemptLimiter(clock=clock)
    app = FastAPI(title="Dispute Intake API", version=resolved.service_version)

    # Middleware: the last one added is the outermost, so the request context wraps the rest
    app.add_middleware(SessionAuthMiddleware, sessions=sessions, public_paths=(TEST_SESSIONS_PATH,))
    app.add_middleware(RequestContextMiddleware)
    _register_error_handlers(app)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        """Report that the process is up; performs no dependency checks."""
        return {"status": "live"}

    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        """Report readiness together with version information."""
        return {
            "status": "ready",
            "service_version": resolved.service_version,
            "environment": resolved.app_env.value,
        }

    # The sandbox login is registered only when it is enabled (never in production)
    test_key = resolved.test_identity_key if resolved.test_identity_enabled else None
    app.include_router(
        build_auth_router(sessions=sessions, test_login_key=test_key, limiter=limiter)
    )

    return app
