"""
Authentication and Error-Format Contract Tests
==============================================

Component: ``app.main.create_app``, ``app.api.auth`` and ``app.security`` middleware. Hermetic:
FastAPI's in-process client, injected settings and an injected clock that only moves when the test
says so; no network and no ``.env``.
"""

from __future__ import annotations

# Standard libraries
import logging  # Capture what the service logs
import re  # Shape of generated request identifiers
from datetime import UTC, datetime, timedelta  # Controlled time
from typing import Any  # JSON bodies

# Third-party libraries
import pytest  # Test runner and fixtures
from fastapi import FastAPI, Request  # Extra routes to prove default deny
from fastapi.testclient import TestClient  # In-process client
from pydantic import SecretStr  # Secrets in injected settings

# Local modules
from app.api.auth import principal_of
from app.config import AppEnvironment, ConfigError, Settings, load_settings
from app.main import create_app

START = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)
LOGIN_KEY = "test-login-key-0123456789"
SIGNING_KEY = "s" * 40
LOGIN = "/v1/auth/test-sessions"


class Clock:
    """A clock that only moves when the test says so."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def _settings(**updates: Any) -> Settings:
    values: dict[str, Any] = {
        "session_signing_key": SecretStr(SIGNING_KEY),
        "test_identity_enabled": True,
        "test_identity_key": SecretStr(LOGIN_KEY),
        "service_version": "test-sha",
    }
    return load_settings(env_file=None).model_copy(update={**values, **updates})


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app(clock: Clock) -> FastAPI:
    application = create_app(_settings(), clock=clock)

    @application.get("/v1/probe")
    def probe(request: Request) -> dict[str, str]:
        """A route added later: it must be protected without anyone remembering to."""
        return {"customer_id": principal_of(request).customer_id}

    @application.get("/v1/boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("secret internal detail: /srv/app/db.py line 42")

    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _login(client: TestClient, customer_id: str = "C1") -> dict[str, Any]:
    response = client.post(
        LOGIN, json={"customer_id": customer_id}, headers={"X-Test-Login-Key": LOGIN_KEY}
    )
    assert response.status_code == 201
    return dict(response.json())


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_problem(response: Any, status: int, code: str, *, reauth: bool) -> dict[str, Any]:
    body = dict(response.json())
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    assert body["code"] == code and body["status"] == status
    assert body["type"] == f"urn:problem:{code}"
    assert body["reauth_required"] is reauth
    assert body["request_id"] == response.headers["x-request-id"]
    return body


# -----------------------------------------------------------------------------
# Sandbox login
# -----------------------------------------------------------------------------


def test_a_trusted_test_client_receives_a_short_lived_session(client: TestClient) -> None:
    """The sandbox login returns a bearer token, its expiry and that it must not be cached."""
    response = client.post(
        LOGIN, json={"customer_id": "CUST-1"}, headers={"X-Test-Login-Key": LOGIN_KEY}
    )

    body = response.json()
    assert response.status_code == 201
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 900
    assert body["expires_at"].startswith("2026-09-26T12:15:00")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_sandbox_login_is_not_available_unless_it_is_enabled(clock: Clock) -> None:
    """Disabled (the default), the route does not exist: 404, not a hint that it is off."""
    plain = TestClient(create_app(_settings(test_identity_enabled=False), clock=clock))

    response = plain.post(
        LOGIN, json={"customer_id": "C1"}, headers={"X-Test-Login-Key": LOGIN_KEY}
    )

    _assert_problem(response, 404, "not_found", reauth=False)


@pytest.mark.parametrize(
    "key",
    [None, "", "wrong", LOGIN_KEY + "x", LOGIN_KEY[:-1], "é" * 25],
    ids=lambda k: repr(k)[:12],
)
def test_the_login_requires_the_shared_secret(client: TestClient, key: str | None) -> None:
    """Without the exact secret no session is issued."""
    headers = {} if key is None else {"X-Test-Login-Key": key.encode("utf-8")}

    response = client.post(LOGIN, json={"customer_id": "C1"}, headers=headers)

    body = _assert_problem(response, 401, "test_login_rejected", reauth=True)
    assert "access_token" not in body


@pytest.mark.parametrize(
    "payload",
    [
        {"document_number": "12345678"},
        {"customer_id": "C1", "document_number": "12345678"},
        {"customer_id": "C1", "name": "Ana Pérez"},
        {"name": "Ana", "document_number": "1", "customer_id": "C1", "password": "x"},
    ],
    ids=["document-only", "customer-plus-document", "customer-plus-name", "many-fields"],
)
def test_an_identifier_the_customer_types_is_never_proof_of_identity(
    client: TestClient, payload: dict[str, str]
) -> None:
    """Only the customer identifier is accepted; a document number is refused, not echoed."""
    response = client.post(LOGIN, json=payload, headers={"X-Test-Login-Key": LOGIN_KEY})

    body = _assert_problem(response, 422, "validation_error", reauth=False)
    assert "access_token" not in body
    rendered = response.text
    for value in ("12345678", "Ana", "document_number", "password"):
        assert value not in rendered


@pytest.mark.parametrize(
    "customer_id", ["", "a" * 21, "has space", "../x", "C1;--", 42, None, ["C1"]]
)
def test_a_malformed_customer_identifier_is_rejected(client: TestClient, customer_id: Any) -> None:
    """The customer identifier has a strict shape before anything is signed."""
    response = client.post(
        LOGIN, json={"customer_id": customer_id}, headers={"X-Test-Login-Key": LOGIN_KEY}
    )

    body = _assert_problem(response, 422, "validation_error", reauth=False)
    assert body["fields"] == ["body.customer_id"]


def test_the_login_secret_is_compared_after_the_body_is_validated(client: TestClient) -> None:
    """A bad body with a good secret and a good body with a bad secret both fail, differently."""
    assert client.post(LOGIN, json={}, headers={"X-Test-Login-Key": LOGIN_KEY}).status_code == 422
    assert client.post(LOGIN, json={"customer_id": "C1"}).status_code == 401


def test_repeated_failures_are_limited_and_the_limit_lifts_with_time(
    client: TestClient, clock: Clock
) -> None:
    """Five failures in a minute block the client, even with the right secret, for a minute."""
    for _ in range(5):
        client.post(LOGIN, json={"customer_id": "C1"}, headers={"X-Test-Login-Key": "wrong"})

    blocked = client.post(
        LOGIN, json={"customer_id": "C1"}, headers={"X-Test-Login-Key": LOGIN_KEY}
    )

    _assert_problem(blocked, 429, "too_many_attempts", reauth=False)
    assert blocked.headers["retry-after"] == "60"
    clock.now = START + timedelta(seconds=60)
    assert _login(client)["token_type"] == "Bearer"


def test_a_successful_login_clears_the_failures(client: TestClient) -> None:
    """Failures before a success do not count against later attempts."""
    for _ in range(4):
        client.post(LOGIN, json={"customer_id": "C1"}, headers={"X-Test-Login-Key": "wrong"})
    _login(client)
    for _ in range(4):
        client.post(LOGIN, json={"customer_id": "C1"}, headers={"X-Test-Login-Key": "wrong"})

    assert _login(client)["token_type"] == "Bearer"


# -----------------------------------------------------------------------------
# Session use
# -----------------------------------------------------------------------------


def test_the_session_says_who_it_belongs_to_from_the_token_alone(client: TestClient) -> None:
    """The customer comes from the signed token, never from the request."""
    token = _login(client, "CUST-7")["access_token"]

    response = client.get("/v1/session", headers=_bearer(token))

    assert response.status_code == 200
    assert response.json()["customer_id"] == "CUST-7"
    assert response.headers["cache-control"] == "no-store"


def test_a_route_added_later_is_protected_by_default(client: TestClient) -> None:
    """Default deny: any new path under /v1 needs a session, and receives the principal."""
    assert client.get("/v1/probe").status_code == 401
    token = _login(client, "CUST-9")["access_token"]

    assert client.get("/v1/probe", headers=_bearer(token)).json() == {"customer_id": "CUST-9"}


def test_a_customer_cannot_choose_whose_data_a_request_acts_on(client: TestClient) -> None:
    """Query, body and header attempts to name another customer change nothing."""
    token = _login(client, "CUST-A")["access_token"]

    response = client.get(
        "/v1/probe?customer_id=CUST-B",
        headers={**_bearer(token), "X-Customer-Id": "CUST-B"},
    )

    assert response.json() == {"customer_id": "CUST-A"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "bearer"},
        {"Authorization": "Bearer a b"},
        {"Authorization": "Bearer " + "a" * 2049},
    ],
    ids=[
        "none",
        "empty",
        "scheme-only",
        "blank-token",
        "basic",
        "lowercase-scheme-only",
        "two-tokens",
        "huge",
    ],
)
def test_a_request_without_a_usable_bearer_token_is_told_to_sign_in(
    client: TestClient, headers: dict[str, str]
) -> None:
    """Missing or malformed credentials give the same structured re-authentication response."""
    response = client.get("/v1/session", headers=headers)

    _assert_problem(response, 401, "session_missing", reauth=True)
    assert response.headers["www-authenticate"] == "Bearer"


def test_the_bearer_scheme_is_case_insensitive(client: TestClient) -> None:
    """``bearer`` and ``BEARER`` are the same scheme."""
    token = _login(client)["access_token"]

    assert (
        client.get("/v1/session", headers={"Authorization": f"bearer {token}"}).status_code == 200
    )


@pytest.mark.parametrize("value", ["not-a-token", "a.b.c", "12345678", "C1"])
def test_an_identifier_or_garbage_in_place_of_a_token_is_invalid(
    client: TestClient, value: str
) -> None:
    """A document number or a customer identifier presented as a token authenticates nothing."""
    response = client.get("/v1/session", headers=_bearer(value))

    _assert_problem(response, 401, "session_invalid", reauth=True)


def test_an_expired_session_gets_the_structured_re_authentication_response(
    client: TestClient, clock: Clock
) -> None:
    """After the lifetime the same token is refused with a code the client can act on."""
    token = _login(client)["access_token"]
    clock.now = START + timedelta(seconds=899)
    assert client.get("/v1/session", headers=_bearer(token)).status_code == 200

    clock.now = START + timedelta(seconds=900)
    response = client.get("/v1/session", headers=_bearer(token))

    body = _assert_problem(response, 401, "session_expired", reauth=True)
    assert body["title"] == "The session has expired"


def test_logout_ends_the_session_immediately_and_only_that_session(client: TestClient) -> None:
    """A revoked token is refused with its own code; the customer's other session keeps working."""
    first, second = _login(client)["access_token"], _login(client)["access_token"]

    assert client.post("/v1/auth/logout", headers=_bearer(first)).status_code == 204
    revoked = client.get("/v1/session", headers=_bearer(first))

    _assert_problem(revoked, 401, "session_revoked", reauth=True)
    assert client.get("/v1/session", headers=_bearer(second)).status_code == 200
    assert client.post("/v1/auth/logout", headers=_bearer(first)).status_code == 401


def test_a_token_from_another_deployment_is_invalid(client: TestClient, clock: Clock) -> None:
    """Sessions signed with another key are refused."""
    other = TestClient(create_app(_settings(session_signing_key=SecretStr("z" * 40)), clock=clock))
    token = _login(other)["access_token"]

    _assert_problem(
        client.get("/v1/session", headers=_bearer(token)), 401, "session_invalid", reauth=True
    )


def test_public_paths_stay_open_and_the_rest_of_the_api_does_not(client: TestClient) -> None:
    """Health checks and the documentation need no session; every /v1 path does."""
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/v1/anything").status_code == 401


# -----------------------------------------------------------------------------
# Error format and request identifiers
# -----------------------------------------------------------------------------


def test_an_unknown_path_is_hidden_from_callers_without_a_session(client: TestClient) -> None:
    """Existence of /v1 paths is not revealed: no session, no 404."""
    _assert_problem(client.get("/v1/unknown"), 401, "session_missing", reauth=True)


def test_an_unknown_path_and_a_wrong_method_are_problems_for_signed_in_callers(
    client: TestClient,
) -> None:
    """404 and 405 use the same format, with the allow header kept."""
    token = _login(client)["access_token"]

    missing = client.get("/v1/unknown", headers=_bearer(token))
    wrong_method = client.post("/v1/session", headers=_bearer(token))

    _assert_problem(missing, 404, "not_found", reauth=False)
    _assert_problem(wrong_method, 405, "method_not_allowed", reauth=False)
    assert "GET" in wrong_method.headers["allow"]


def test_an_unexpected_failure_reaches_the_client_as_a_generic_problem_and_is_logged_once(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """No trace, path or message leaves the service; the log has the trace and the request id."""
    token = _login(client)["access_token"]

    with caplog.at_level(logging.ERROR):
        response = client.get("/v1/boom", headers=_bearer(token))

    body = _assert_problem(response, 500, "internal_error", reauth=False)
    assert "secret internal detail" not in response.text and "db.py" not in response.text
    assert "Traceback" not in response.text
    errors = [
        r for r in caplog.records if r.name == "app.main" and "unhandled_error" in r.getMessage()
    ]
    assert len(errors) == 1
    assert body["request_id"] in errors[0].getMessage()
    assert errors[0].exc_info is not None


def test_a_client_supplied_request_id_with_a_safe_shape_is_kept(client: TestClient) -> None:
    """The identifier travels from request to response and into problem bodies."""
    response = client.get("/v1/session", headers={"X-Request-ID": "trace-abc-12345"})

    body = _assert_problem(response, 401, "session_missing", reauth=True)
    assert body["request_id"] == "trace-abc-12345"


@pytest.mark.parametrize(
    "supplied", ["short", "has space in it", "x" * 65, "semi;colon;abc", "é" * 10]
)
def test_an_unsafe_request_id_is_replaced(client: TestClient, supplied: str) -> None:
    """A client cannot put arbitrary text into the logs through the identifier."""
    response = client.get("/health/live", headers={"X-Request-ID": supplied.encode("utf-8")})

    assert re.fullmatch(r"req_[0-9a-f]{16}", response.headers["x-request-id"])


def test_every_response_has_a_request_id_and_nosniff(client: TestClient) -> None:
    """Also on health checks and on documentation."""
    for path in ("/health/live", "/openapi.json", "/v1/session"):
        response = client.get(path)
        assert response.headers["x-request-id"]
        assert response.headers["x-content-type-options"] == "nosniff"


# -----------------------------------------------------------------------------
# Secrets stay out of logs, and start-up rules
# -----------------------------------------------------------------------------


def test_no_secret_reaches_the_logs(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    """Tokens, the login secret and the signing key are never logged, even on failures."""
    with caplog.at_level(logging.DEBUG):
        token = _login(client, "CUST-LOG")["access_token"]
        client.get("/v1/session", headers=_bearer(token))
        client.get("/v1/session", headers=_bearer(token + "x"))
        client.post(
            LOGIN, json={"customer_id": "CUST-LOG"}, headers={"X-Test-Login-Key": "wrong-secret"}
        )
        client.post("/v1/auth/logout", headers=_bearer(token))

    logged = " ".join(record.getMessage() for record in caplog.records)
    for secret in (token, LOGIN_KEY, SIGNING_KEY, "wrong-secret"):
        assert secret not in logged
    assert "session_issued" in logged and "auth_rejected" in logged and "session_revoked" in logged


def test_without_a_signing_key_local_runs_with_a_throw_away_key_and_others_refuse_to_start(
    clock: Clock,
) -> None:
    """Local convenience never becomes a production default."""
    local = create_app(
        _settings(session_signing_key=None, app_env=AppEnvironment.LOCAL), clock=clock
    )
    other = create_app(
        _settings(session_signing_key=None, app_env=AppEnvironment.LOCAL), clock=clock
    )
    token = _login(TestClient(local))["access_token"]

    assert TestClient(other).get("/v1/session", headers=_bearer(token)).status_code == 401
    for environment in (AppEnvironment.DEV, AppEnvironment.PROD):
        with pytest.raises(ConfigError, match="SESSION_SIGNING_KEY"):
            create_app(
                _settings(
                    session_signing_key=None, app_env=environment, test_identity_enabled=False
                ),
                clock=clock,
            )


# -----------------------------------------------------------------------------
# Defence in depth and the production clock
# -----------------------------------------------------------------------------


def test_a_route_that_needs_a_principal_refuses_when_the_middleware_did_not_run(
    app: FastAPI,
) -> None:
    """Mounted outside /v1 (a wiring mistake), a route still cannot act without a principal."""

    @app.get("/internal/who")
    def who(request: Request) -> dict[str, str]:
        return {"customer_id": principal_of(request).customer_id}

    response = TestClient(app, raise_server_exceptions=False).get("/internal/who")

    _assert_problem(response, 401, "session_missing", reauth=True)


def test_the_application_starts_and_stops_cleanly_and_serves_between(app: FastAPI) -> None:
    """Lifespan events pass through the middleware untouched."""
    with TestClient(app) as running:
        assert running.get("/health/live").status_code == 200


def test_the_production_clock_is_timezone_aware_utc() -> None:
    """The default clock returns UTC, close to the real time."""
    from app.security.sessions import utc_now  # noqa: PLC0415 - only this test needs it

    moment = utc_now()

    assert moment.tzinfo is UTC
    assert abs((datetime.now(UTC) - moment).total_seconds()) < 5
