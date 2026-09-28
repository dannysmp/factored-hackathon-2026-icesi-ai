"""
Error Format
============

Overview
--------
The single format every failure of the service uses: an RFC 9457 problem document with a stable
machine-readable code, a safe message and the request identifier. Clients act on the code; humans
read the title and the identifier when they ask for support.

Scope
-----
In: the error codes, the exception routes raise, and the response builder.
Out: deciding when a failure happens (routes, middleware) and logging it.

Design Principles
-----------------
- Codes are a stable contract: they are added, never renamed or reused.
- A message never carries a value from the request, a stack trace, a path or a query: what the
  client sent is not echoed, and internal detail stays in the logs.
- A failure that requires the customer to sign in again says so with ``reauth_required``.

Runtime Contract
----------------
``ProblemError(code, status, title, detail, ...)`` raised by routes and middleware.
``problem_response(problem, request_id) -> JSONResponse`` builds the response.

Limitations
-----------
Problem ``type`` values are URNs, not dereferenceable pages; a documentation site can map them
later without changing clients.
"""

from __future__ import annotations

# Standard libraries
from enum import StrEnum  # Closed set of error codes

# Third-party libraries
from starlette.responses import JSONResponse  # Problem document response

PROBLEM_CONTENT_TYPE = "application/problem+json"


class ErrorCode(StrEnum):
    """Stable codes of every failure the service reports."""

    SESSION_MISSING = "session_missing"
    SESSION_INVALID = "session_invalid"
    SESSION_EXPIRED = "session_expired"
    SESSION_REVOKED = "session_revoked"
    TEST_LOGIN_REJECTED = "test_login_rejected"
    DEMO_SIGNIN_REJECTED = "demo_signin_rejected"
    TOO_MANY_ATTEMPTS = "too_many_attempts"
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    REQUEST_REFUSED = "request_refused"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INTERNAL_ERROR = "internal_error"
    TURN_CONFLICT = "turn_conflict"


class ProblemError(Exception):
    """A failure to report to the client as a problem document.

    Attributes
    ----------
    code : ErrorCode
        Stable machine-readable code.
    status : int
        HTTP status.
    title : str
        Short, safe, human-readable summary.
    detail : str | None
        Safe explanation; never contains request data.
    reauth_required : bool
        True when the customer must sign in again.
    headers : dict[str, str]
        Extra response headers (for example ``WWW-Authenticate`` or ``Retry-After``).
    fields : tuple[str, ...]
        Names of the request fields at fault, for validation failures; never their values.
    """

    def __init__(
        self,
        code: ErrorCode,
        status: int,
        title: str,
        detail: str | None = None,
        *,
        reauth_required: bool = False,
        headers: dict[str, str] | None = None,
        fields: tuple[str, ...] = (),
    ) -> None:
        super().__init__(f"{code.value}: {title}")
        self.code = code
        self.status = status
        self.title = title
        self.detail = detail
        self.reauth_required = reauth_required
        self.headers = headers or {}
        self.fields = fields


def problem_response(problem: ProblemError, request_id: str) -> JSONResponse:
    """Build the problem document response for ``problem``.

    Parameters
    ----------
    problem : ProblemError
        The failure to report.
    request_id : str
        Identifier of the request, returned so support can find it in the logs.
    """
    body: dict[str, object] = {
        "type": f"urn:problem:{problem.code.value}",
        "title": problem.title,
        "status": problem.status,
        "code": problem.code.value,
        "request_id": request_id,
        "reauth_required": problem.reauth_required,
    }
    if problem.detail is not None:
        body["detail"] = problem.detail
    if problem.fields:
        body["fields"] = list(problem.fields)
    # Every problem document carries the same protective headers, whichever layer produced it
    headers = {
        "X-Request-ID": request_id,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        **problem.headers,
    }
    return JSONResponse(
        body, status_code=problem.status, headers=headers, media_type=PROBLEM_CONTENT_TYPE
    )
