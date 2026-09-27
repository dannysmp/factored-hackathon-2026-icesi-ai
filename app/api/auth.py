"""
Authentication Routes
=====================

Overview
--------
The routes that create, describe and end a session: a sandbox login for test clients, a call that
says who the current session belongs to, and logout.

Scope
-----
In: the three routes and their request and response models.
Out: verifying tokens on other routes (``SessionAuthMiddleware``) and real identity proofing.

Trusted-Session Contract
------------------------
There is no customer-facing login here. A real deployment receives sessions from the bank's
identity provider; this service only verifies them. For sandbox use, ``POST /v1/auth/test-sessions``
stands in for that provider:

- it exists only when ``TEST_IDENTITY_ENABLED`` is true (never in production); when it is off the
  path is protected like every other ``/v1`` path, so it reveals nothing;
- the caller proves it is a trusted test client with the shared secret in ``X-Test-Login-Key``,
  checked (and the attempt counted) before the body is looked at;
- the body carries only ``customer_id``. A document number, a name or any other field is
  rejected: **an identifier the customer types is never proof of identity**;
- a ``customer_id`` the store does not know gets the same refusal as a wrong shared secret
  (AC-E4-47): the two are indistinguishable by response code, message or shape;
- a customer the store does know always gets a session, whatever their status — the policy
  applies no status gate (AC-E4-48), and the login boundary does not invent one either;
- failed attempts are limited per client address.

Runtime Contract
----------------
``POST /v1/auth/test-sessions``  body ``{"customer_id": str}``  -> 201 session (public)
``GET  /v1/session``             -> 200 who the session belongs to
``POST /v1/auth/logout``         -> 204, the session stops working

Limitations
-----------
Sessions are not refreshed: an expired session needs a new login.
"""

from __future__ import annotations

# Standard libraries
import hmac  # Constant-time comparison of the shared secret
import logging  # Structured events about sessions
from collections.abc import Callable  # Type of the injected customer lookup
from datetime import datetime  # Expiry in responses
from typing import Annotated  # Header parameter declaration

# Third-party libraries
from fastapi import APIRouter, Depends, Header, Request, Response  # Routing and request access
from pydantic import BaseModel, ConfigDict, Field, SecretStr  # Validated models

# Local modules
from app.security.errors import ErrorCode, ProblemError  # Failure format
from app.security.limits import AttemptLimiter  # Failed-attempt limit
from app.security.middleware import current_request_id  # Request identifier for logs
from app.security.sessions import CUSTOMER_ID_PATTERN, Principal, SessionService  # Sessions

logger = logging.getLogger(__name__)

TEST_SESSIONS_PATH = "/v1/auth/test-sessions"

# The customer's status as the store records it, or None when there is no match (AC-E4-47); the
# sandbox login checks existence only, never status (AC-E4-48).
CustomerLookup = Callable[[str], str | None]

# The model's pattern is a search, not a full match, so it is anchored explicitly.
ANCHORED_CUSTOMER_ID = f"^{CUSTOMER_ID_PATTERN.pattern}$"


class TestLoginRequest(BaseModel):
    """Body of the sandbox login: the customer the test client acts as, and nothing else."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    __test__ = False  # A model, not a pytest test class

    customer_id: Annotated[str, Field(min_length=1, max_length=20, pattern=ANCHORED_CUSTOMER_ID)]


class SessionResponse(BaseModel):
    """A newly issued session."""

    access_token: str
    token_type: str = "Bearer"  # noqa: S105 - the name of the scheme, not a secret
    expires_at: datetime
    expires_in: int


class SessionInfo(BaseModel):
    """Who the current session belongs to."""

    customer_id: str
    session_id: str
    expires_at: datetime


def principal_of(request: Request) -> Principal:
    """The principal the authentication middleware attached to the request.

    Raises
    ------
    ProblemError
        ``session_missing`` when a route is reached without one (a wiring mistake, refused).
    """
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise ProblemError(
            ErrorCode.SESSION_MISSING,
            401,
            "Authentication is required",
            "Sign in again to continue.",
            reauth_required=True,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def build_auth_router(
    *,
    sessions: SessionService,
    test_login_key: SecretStr | None,
    limiter: AttemptLimiter,
    customer_lookup: CustomerLookup = lambda customer_id: None,
) -> APIRouter:
    """Build the authentication routes.

    Parameters
    ----------
    sessions : SessionService
        Issues and revokes session tokens.
    test_login_key : SecretStr | None
        Shared secret of the sandbox login; when None the sandbox login is not registered.
    limiter : AttemptLimiter
        Limits failed sandbox logins per client address.
    customer_lookup : CustomerLookup
        Answers whether a customer exists (AC-E4-47); ignored when the sandbox login is not
        registered. Defaults to "nobody exists" so a caller that forgets to inject one for an
        enabled sandbox login fails closed rather than accepting every identifier.
    """
    router = APIRouter()

    if test_login_key is not None:
        expected = test_login_key.get_secret_value().encode("utf-8")

        def authorize_test_client(
            request: Request,
            x_test_login_key: Annotated[str | None, Header()] = None,
        ) -> None:
            """Refuse the sandbox login unless the caller is limited-in and holds the secret.

            Runs before the body is validated, so an unauthenticated caller learns nothing about
            the body's shape. The attempt is counted first and cleared on success, which makes
            the limit hold under concurrent attempts.
            """
            client = request.client.host if request.client else "unknown"
            wait = limiter.begin_attempt(client)
            if wait:
                logger.warning("test_login_limited request_id=%s", current_request_id())
                raise ProblemError(
                    ErrorCode.TOO_MANY_ATTEMPTS,
                    429,
                    "Too many failed attempts",
                    "Wait before trying again.",
                    headers={"Retry-After": str(wait)},
                )
            supplied = (x_test_login_key or "").encode("utf-8")
            if not hmac.compare_digest(supplied, expected):
                logger.warning("test_login_rejected request_id=%s", current_request_id())
                raise ProblemError(
                    ErrorCode.TEST_LOGIN_REJECTED,
                    401,
                    "Sign-in was refused",
                    reauth_required=True,
                )
            limiter.reset(client)

        @router.post(
            TEST_SESSIONS_PATH, status_code=201, dependencies=[Depends(authorize_test_client)]
        )
        def create_test_session(body: TestLoginRequest) -> SessionResponse:
            """Issue a session for a trusted test client (sandbox only).

            AC-E4-47: a customer the store does not know gets the same refusal as a wrong shared
            secret; a customer it does know always gets a session, whatever their status
            (AC-E4-48 — no status gate, here or in the policy).
            """
            if customer_lookup(body.customer_id) is None:
                logger.warning("test_login_unknown_customer request_id=%s", current_request_id())
                raise ProblemError(
                    ErrorCode.TEST_LOGIN_REJECTED,
                    401,
                    "Sign-in was refused",
                    reauth_required=True,
                )
            issued = sessions.issue(body.customer_id)
            logger.info(
                "session_issued session_id=%s request_id=%s",
                issued.session_id,
                current_request_id(),
            )
            ttl = int((issued.expires_at - sessions.now()).total_seconds())
            return SessionResponse(
                access_token=issued.token, expires_at=issued.expires_at, expires_in=ttl
            )

    @router.get("/v1/session")
    def current_session(request: Request) -> SessionInfo:
        """Say who the current session belongs to and when it ends."""
        principal = principal_of(request)
        return SessionInfo(
            customer_id=principal.customer_id,
            session_id=principal.session_id,
            expires_at=principal.expires_at,
        )

    @router.post("/v1/auth/logout", status_code=204)
    def logout(request: Request) -> Response:
        """End the current session: its token stops working immediately."""
        principal = principal_of(request)
        try:
            sessions.revoke(principal)
        except RuntimeError:
            # The revocation store is full: say so, and leave the session as it was
            raise ProblemError(
                ErrorCode.SERVICE_UNAVAILABLE,
                503,
                "The session could not be ended",
                "Try again shortly.",
                headers={"Retry-After": "60"},
            ) from None
        logger.info(
            "session_revoked session_id=%s request_id=%s",
            principal.session_id,
            current_request_id(),
        )
        return Response(status_code=204)

    return router
