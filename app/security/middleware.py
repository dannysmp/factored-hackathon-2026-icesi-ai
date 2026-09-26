"""
Request Context and Authentication Middleware
=============================================

Overview
--------
Two pieces of ASGI middleware. The first gives every request an identifier that appears on the
response and in the logs. The second resolves the session behind every request to the versioned
API and refuses the request, in the standard error format, when there is no valid session.

Scope
-----
In: request identifiers, security headers, the bearer-token check and the principal handed to
routes.
Out: issuing tokens (``sessions``), the routes, and what a customer may do once authenticated.

Design Principles
-----------------
- Default deny. Every path under ``/v1/`` requires a session unless it is listed as public, so a
  route added later is protected without anyone remembering to protect it.
- The failure says what the client can do: ``reauth_required`` is true for a missing, invalid,
  expired or revoked session.
- A supplied request identifier is used only if it has a safe shape; otherwise a new one is
  generated, so a client cannot inject text into the logs.
- Nothing about the token is logged: only the reason code and the request identifier.

Runtime Contract
----------------
``RequestContextMiddleware(app)`` sets ``scope["state"]["request_id"]`` and the ``X-Request-ID``
header.
``SessionAuthMiddleware(app, sessions, public_paths)`` sets ``scope["state"]["principal"]``.
``current_request_id()`` returns the identifier of the request being handled.

Limitations
-----------
Only the ``Authorization: Bearer`` scheme is accepted; cookies are not read.
"""

from __future__ import annotations

# Standard libraries
import logging  # Structured events about refused sessions
import re  # Safe shape of a client-supplied request identifier
import secrets  # Generated request identifiers
from collections.abc import Iterable, MutableMapping  # Types of ASGI messages
from contextvars import ContextVar  # Request identifier visible to loggers

# Third-party libraries
from starlette.types import ASGIApp, Message, Receive, Scope, Send  # ASGI protocol types

# Local modules
from app.security.errors import ErrorCode, ProblemError, problem_response  # Failure format
from app.security.sessions import SessionRejected, SessionService  # Session verification

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "x-request-id"
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{8,64}")
API_PREFIX = "/v1/"
MAX_TOKEN_LENGTH = 2048

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

_REJECTION_TITLES: dict[ErrorCode, str] = {
    ErrorCode.SESSION_MISSING: "Authentication is required",
    ErrorCode.SESSION_INVALID: "The session is not valid",
    ErrorCode.SESSION_EXPIRED: "The session has expired",
    ErrorCode.SESSION_REVOKED: "The session was ended",
}


def current_request_id() -> str:
    """Identifier of the request being handled, or ``-`` outside a request."""
    return _request_id.get()


def _state(scope: Scope) -> MutableMapping[str, object]:
    """The mutable per-request state shared with routes."""
    state: MutableMapping[str, object] = scope.setdefault("state", {})
    return state


def _header(scope: Scope, name: str) -> str | None:
    """The first value of a request header, decoded as text, or None."""
    for key, value in scope["headers"]:
        if key.decode("latin-1").lower() == name:
            return str(value.decode("latin-1"))
    return None


class RequestContextMiddleware:
    """Assigns a request identifier and adds the headers every response carries."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one request."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Use the client's identifier only when it has a safe shape
        supplied = _header(scope, REQUEST_ID_HEADER)
        request_id = (
            supplied
            if supplied is not None and REQUEST_ID_PATTERN.fullmatch(supplied)
            else f"req_{secrets.token_hex(8)}"
        )
        _state(scope)["request_id"] = request_id
        token = _request_id.set(request_id)
        api_call = scope["path"].startswith(API_PREFIX)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {key.lower() for key, _ in headers}
                extra = [(b"x-content-type-options", b"nosniff")]
                if b"x-request-id" not in present:
                    extra.append((b"x-request-id", request_id.encode("latin-1")))
                if api_call:
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": [*headers, *extra]}
            await send(message)

        try:
            await self._app(scope, receive, send_with_headers)
        finally:
            _request_id.reset(token)


def _bearer_token(scope: Scope) -> str:
    """The bearer token of a request.

    Raises
    ------
    ProblemError
        ``session_missing`` when there is no usable ``Authorization: Bearer`` header.
    """
    header = _header(scope, "authorization")
    if header is None:
        raise _rejection(ErrorCode.SESSION_MISSING)
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token or " " in token or len(token) > MAX_TOKEN_LENGTH:
        raise _rejection(ErrorCode.SESSION_MISSING)
    return token


def _rejection(code: ErrorCode) -> ProblemError:
    """The problem for a refused session; the client should sign in again."""
    return ProblemError(
        code,
        401,
        _REJECTION_TITLES[code],
        "Sign in again to continue.",
        reauth_required=True,
        headers={"WWW-Authenticate": "Bearer"},
    )


class SessionAuthMiddleware:
    """Requires a valid session for every path under ``/v1/`` that is not public."""

    def __init__(
        self, app: ASGIApp, sessions: SessionService, public_paths: Iterable[str] = ()
    ) -> None:
        self._app = app
        self._sessions = sessions
        self._public = frozenset(public_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one request."""
        path = scope["path"] if scope["type"] == "http" else ""
        if not path.startswith(API_PREFIX) or path in self._public:
            await self._app(scope, receive, send)
            return

        # Resolve the session or answer with the standard problem document
        try:
            principal = self._sessions.verify(_bearer_token(scope))
        except SessionRejected as rejected:
            problem = _rejection(rejected.code)
        except ProblemError as failure:
            problem = failure
        else:
            _state(scope)["principal"] = principal
            await self._app(scope, receive, send)
            return

        logger.warning(
            "auth_rejected code=%s request_id=%s", problem.code.value, current_request_id()
        )
        response = problem_response(problem, str(_state(scope).get("request_id", "-")))
        await response(scope, receive, send)
