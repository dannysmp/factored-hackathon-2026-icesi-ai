"""
Session Authentication Middleware Tests
=========================================

Component: ``app.security.middleware.SessionAuthMiddleware``. Hermetic: a minimal Starlette app,
a real ``SessionService`` with fixed test keys, and FastAPI's in-process test client; no database,
no LLM call.
"""

from __future__ import annotations

# Third-party libraries
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

# Local modules
from app.security.errors import ErrorCode
from app.security.middleware import RequestContextMiddleware, SessionAuthMiddleware
from app.security.sessions import AgentPrincipal, Principal, SessionService

_CUSTOMER_KEY = SecretStr("k" * 40)
_AGENT_KEY = SecretStr("g" * 40)


async def _whoami(request: Request) -> JSONResponse:
    principal = request.state.principal
    return JSONResponse({"type": type(principal).__name__})


def _service() -> SessionService:
    return SessionService({"customer": _CUSTOMER_KEY, "agent": _AGENT_KEY}, 900)


def _app(sessions: SessionService, audience_by_prefix: dict[str, str]) -> Starlette:
    app = Starlette(routes=[Route("/v1/whoami", _whoami)])
    app.add_middleware(
        SessionAuthMiddleware, sessions=sessions, audience_by_prefix=audience_by_prefix
    )
    app.add_middleware(RequestContextMiddleware)
    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_a_customer_token_reaches_a_customer_audience_path_as_a_principal() -> None:
    sessions = _service()
    client = TestClient(_app(sessions, {"/v1": "customer"}))
    token = sessions.issue("C1", audience="customer").token

    response = client.get("/v1/whoami", headers=_bearer(token))

    assert response.status_code == 200
    assert response.json() == {"type": Principal.__name__}


def test_an_agent_token_reaches_an_agent_audience_path_as_an_agent_principal() -> None:
    sessions = _service()
    client = TestClient(_app(sessions, {"/v1": "agent"}))
    token = sessions.issue("A1", audience="agent").token

    response = client.get("/v1/whoami", headers=_bearer(token))

    assert response.status_code == 200
    assert response.json() == {"type": AgentPrincipal.__name__}


def test_an_agent_token_on_a_customer_audience_path_is_refused_like_no_session() -> None:
    """ADR-18: a validly signed token of the wrong audience learns nothing about the path."""
    sessions = _service()
    client = TestClient(_app(sessions, {"/v1": "customer"}))
    agent_token = sessions.issue("A1", audience="agent").token

    response = client.get("/v1/whoami", headers=_bearer(agent_token))

    assert response.status_code == 401
    assert response.json()["code"] == ErrorCode.SESSION_INVALID.value


def test_a_customer_token_on_an_agent_audience_path_is_refused_like_no_session() -> None:
    sessions = _service()
    client = TestClient(_app(sessions, {"/v1": "agent"}))
    customer_token = sessions.issue("C1", audience="customer").token

    response = client.get("/v1/whoami", headers=_bearer(customer_token))

    assert response.status_code == 401
    assert response.json()["code"] == ErrorCode.SESSION_INVALID.value


def test_longest_matching_prefix_picks_the_audience_for_a_more_specific_path() -> None:
    """A prefix map with both ``/v1`` (customer) and a more specific ``/v1/agent`` (agent) sends
    an agent token to the specific route and refuses it on the general one."""
    sessions = _service()
    app = Starlette(routes=[Route("/v1/whoami", _whoami), Route("/v1/agent/whoami", _whoami)])
    app.add_middleware(
        SessionAuthMiddleware,
        sessions=sessions,
        audience_by_prefix={"/v1": "customer", "/v1/agent": "agent"},
    )
    app.add_middleware(RequestContextMiddleware)
    client = TestClient(app)
    agent_token = sessions.issue("A1", audience="agent").token

    on_agent_path = client.get("/v1/agent/whoami", headers=_bearer(agent_token))
    on_general_path = client.get("/v1/whoami", headers=_bearer(agent_token))

    assert on_agent_path.status_code == 200
    assert on_agent_path.json() == {"type": AgentPrincipal.__name__}
    assert on_general_path.status_code == 401


def test_a_protected_path_with_no_configured_audience_is_refused_not_silently_accepted() -> None:
    """Fail closed: a protected path this deployment forgot to map to an audience refuses every
    token, rather than accepting whichever audience happens to verify."""
    sessions = _service()
    client = TestClient(_app(sessions, {}))
    token = sessions.issue("C1", audience="customer").token

    response = client.get("/v1/whoami", headers=_bearer(token))

    assert response.status_code == 401
    assert response.json()["code"] == ErrorCode.SESSION_INVALID.value


def test_a_path_mapped_to_an_unrecognized_audience_is_refused() -> None:
    """A configuration mistake — a prefix mapped to something other than "customer" or "agent" —
    is refused like any other authentication failure, not treated as an open audience."""
    sessions = _service()
    client = TestClient(_app(sessions, {"/v1": "unknown-audience"}))
    token = sessions.issue("C1", audience="customer").token

    response = client.get("/v1/whoami", headers=_bearer(token))

    assert response.status_code == 401
    assert response.json()["code"] == ErrorCode.SESSION_INVALID.value


def test_a_missing_bearer_token_is_refused() -> None:
    sessions = _service()
    client = TestClient(_app(sessions, {"/v1": "customer"}))

    response = client.get("/v1/whoami")

    assert response.status_code == 401
    assert response.json()["code"] == ErrorCode.SESSION_MISSING.value
