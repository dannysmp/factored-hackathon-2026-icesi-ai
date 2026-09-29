"""
Request Context and Authentication Middleware
=============================================

Overview
--------
Three pieces of ASGI middleware, applied in order. The first gives every request an identifier
that appears on the response and in the logs. The second refuses a request whose body is too
large before anything downstream spends work on it. The third resolves the session behind every
request to the versioned API and refuses the request, in the standard error format, when there is
no valid session.

Scope
-----
In: request identifiers, security headers, the body-size cap, the bearer-token check and the
principal handed to routes.
Out: issuing tokens (``sessions``), the routes, and what a customer or agent may do once
authenticated.

Design Principles
-----------------
- Default deny. ``/v1`` and every path under it (HTTP and WebSocket) require a session unless
  listed as public, so a route added later is protected without anyone remembering to. The
  decision uses one normalisation of the path (without ``root_path``, slashes collapsed, lower
  case) so no spelling of a protected path escapes it.
- **A valid token of the wrong audience is refused exactly like no session at all** (ADR-18): a
  path-prefix-to-audience map decides which audience a path requires, by longest matching prefix,
  and that audience picks which of ``SessionService``'s two verify methods is even attempted — a
  customer token reaching an agent-only path is refused by ``verify_agent`` itself and learns
  nothing about what that path is.
- The failure says what the client can do: ``reauth_required`` is true for a missing, invalid,
  expired or revoked session.
- A supplied request identifier is used only if it has a safe shape; otherwise a new one is
  generated, so a client cannot inject text into the logs.
- Nothing about the token is logged: only the reason code and the request identifier.
- The body-size cap runs before session authentication: an oversized body is refused before a JWT
  is ever verified, and it counts bytes actually read off the wire, never a client-stated
  ``Content-Length`` alone.

Runtime Contract
----------------
``RequestContextMiddleware(app)`` sets ``scope["state"]["request_id"]`` and the ``X-Request-ID``
header.
``BodySizeLimitMiddleware(app, max_bytes=MAX_BODY_BYTES)`` refuses a request body over the limit
with ``PAYLOAD_TOO_LARGE`` (413).
``SessionAuthMiddleware(app, sessions, audience_by_prefix, public_paths)`` sets
``scope["state"]["principal"]``.
``current_request_id()`` returns the identifier of the request being handled.
``current_session_id()`` returns the authenticated principal's session id, or ``None`` outside an
authenticated request — read by the structured-logging filter so every line carries it without
every call site passing it explicitly.

Limitations
-----------
Only the ``Authorization: Bearer`` scheme is accepted; cookies are not read. A request refused for
an oversized body is not drained further before the response is sent; a client on a keep-alive
connection may see it close rather than a clean pipelined reply to its next request.
"""

from __future__ import annotations

# Standard libraries
import logging  # Structured events about refused sessions
import re  # Safe shape of a client-supplied request identifier
from collections.abc import Iterable, Mapping, MutableMapping  # Types of ASGI messages
from contextvars import ContextVar  # Request identifier visible to loggers

# Third-party libraries
from starlette.types import ASGIApp, Message, Receive, Scope, Send  # ASGI protocol types

# Local modules
from app.llm.masking import safe_hex_suffix  # A suffix that can't look card-shaped in a log line
from app.security.errors import ErrorCode, ProblemError, problem_response  # Failure format
from app.security.sessions import (  # Session verification
    AgentPrincipal,
    Principal,
    SessionRejected,
    SessionService,
)

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "x-request-id"
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{8,64}")
API_PREFIX = "/v1"
MAX_TOKEN_LENGTH = 2048
MAX_BODY_BYTES = 64 * 1024

_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)

_REJECTION_TITLES: dict[ErrorCode, str] = {
    ErrorCode.SESSION_MISSING: "Authentication is required",
    ErrorCode.SESSION_INVALID: "The session is not valid",
    ErrorCode.SESSION_EXPIRED: "The session has expired",
    ErrorCode.SESSION_REVOKED: "The session was ended",
}


def route_path(scope: Scope) -> str:
    """The path the router will match: without ``root_path``, slashes collapsed, lower case.

    The protected-path decision uses this one normalisation, so a request cannot reach a route
    under a spelling the check did not recognise.
    """
    path: str = scope.get("path", "")
    root: str = scope.get("root_path", "")
    if root and path.startswith(root):
        path = path[len(root) :]
    return re.sub(r"/+", "/", path).lower()


def is_protected(path: str) -> bool:
    """Whether ``path`` (already normalised by ``route_path``) is part of the versioned API."""
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


def current_request_id() -> str:
    """Identifier of the request being handled, or ``-`` outside a request."""
    return _request_id.get()


def current_session_id() -> str | None:
    """The authenticated principal's session id, or ``None`` outside an authenticated request."""
    return _session_id.get()


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
            else f"req_{safe_hex_suffix(nbytes=8).lower()}"
        )
        _state(scope)["request_id"] = request_id
        token = _request_id.set(request_id)
        api_call = is_protected(route_path(scope))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {key.lower() for key, _ in headers}
                wanted = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-request-id", request_id.encode("latin-1")),
                ]
                if api_call:
                    wanted.append((b"cache-control", b"no-store"))
                extra = [(key, value) for key, value in wanted if key not in present]
                message = {**message, "headers": [*headers, *extra]}
            await send(message)

        try:
            await self._app(scope, receive, send_with_headers)
        finally:
            _request_id.reset(token)


class BodySizeLimitMiddleware:
    """Refuses an HTTP request whose body exceeds ``MAX_BODY_BYTES``.

    Counts bytes actually read off the wire, never a client-supplied ``Content-Length`` alone (a
    header a client can omit or understate, notably under chunked transfer encoding). The whole
    body is drained and buffered while counting, up to the limit; a request within it is replayed
    to the rest of the application unchanged, so nothing downstream can tell this middleware ran.
    Placed before session authentication (an oversized body is refused before a JWT is ever
    verified), after the request-context middleware (so the refusal still carries a request id).
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one request; other scopes (WebSocket, lifespan) pass through unchanged."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        buffered: list[Message] = []
        total = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            total += len(message.get("body", b""))
            if total > self._max_bytes:
                logger.warning("body_too_large request_id=%s", current_request_id())
                problem = ProblemError(
                    ErrorCode.PAYLOAD_TOO_LARGE,
                    413,
                    "The request body is too large",
                    f"The body must be at most {self._max_bytes} bytes.",
                )
                request_id = str(_state(scope).get("request_id", "-"))
                response = problem_response(problem, request_id)
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        index = 0

        async def replay_receive() -> Message:
            nonlocal index
            if index < len(buffered):
                message = buffered[index]
                index += 1
                return message
            return await receive()

        await self._app(scope, replay_receive, send)


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


def _required_audience(path: str, audience_by_prefix: Mapping[str, str]) -> str | None:
    """The audience required for ``path``, by longest matching prefix; ``None`` if none applies."""
    best: str | None = None
    best_length = -1
    for prefix, audience in audience_by_prefix.items():
        matches = path == prefix or path.startswith(prefix + "/")
        if matches and len(prefix) > best_length:
            best, best_length = audience, len(prefix)
    return best


class SessionAuthMiddleware:
    """Requires a valid session of the right audience for ``/v1`` and everything under it that is
    not public.

    Which audience a path requires decides which of ``SessionService``'s two audience-specific
    verify methods is even attempted (ADR-18): a customer token presented on an agent-only path
    is refused by ``verify_agent`` itself, exactly like a missing session, without a separate
    after-the-fact audience comparison.
    """

    def __init__(
        self,
        app: ASGIApp,
        sessions: SessionService,
        audience_by_prefix: Mapping[str, str],
        public_paths: Iterable[str] = (),
    ) -> None:
        self._app = app
        self._sessions = sessions
        self._audience_by_prefix = dict(audience_by_prefix)
        self._public = frozenset(public_paths)

    def _verify(self, audience: str, token: str) -> Principal | AgentPrincipal:
        """Verify ``token`` against ``audience``'s own method; an unconfigured audience is a
        deployment mistake, refused exactly like an invalid token rather than silently accepted."""
        if audience == "customer":
            return self._sessions.verify_customer(token)
        if audience == "agent":
            return self._sessions.verify_agent(token)
        raise SessionRejected(ErrorCode.SESSION_INVALID)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one HTTP request or WebSocket connection; other scopes (lifespan) pass through."""
        kind = scope["type"]
        path = route_path(scope)
        if kind not in ("http", "websocket") or not is_protected(path) or path in self._public:
            await self._app(scope, receive, send)
            return
        required_audience = _required_audience(path, self._audience_by_prefix)

        # Resolve the session or refuse with the standard problem document. A protected path with
        # no configured audience is refused the same way (fail closed), rather than accepted.
        try:
            if required_audience is None:
                raise SessionRejected(ErrorCode.SESSION_INVALID)
            principal = self._verify(required_audience, _bearer_token(scope))
        except SessionRejected as rejected:
            problem = _rejection(rejected.code)
        except ProblemError as failure:
            problem = failure
        else:
            _state(scope)["principal"] = principal
            token = _session_id.set(principal.session_id)
            try:
                await self._app(scope, receive, send)
            finally:
                _session_id.reset(token)
            return

        logger.warning(
            "auth_rejected code=%s request_id=%s", problem.code.value, current_request_id()
        )
        if kind == "websocket":
            # Policy violation: refuse before the connection is accepted
            await send({"type": "websocket.close", "code": 1008})
            return
        response = problem_response(problem, str(_state(scope).get("request_id", "-")))
        await response(scope, receive, send)
