"""
Structured Logging Tests
==========================

Component: ``app.observability.logging``. Hermetic: a real root logger and a real
``logging.StreamHandler``, redirected to an in-memory buffer this suite controls directly (not
pytest's ``capsys``: a handler installed by ``configure_logging`` binds whatever ``sys.stderr`` is
at that moment, and that binding can outlive a later ``capsys`` read, so tests read the handler's
own stream instead of relying on file-descriptor capture timing).
"""

from __future__ import annotations

import io
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings, load_settings
from app.main import create_app
from app.observability.logging import configure_logging

# A well-known, Luhn-valid test card number (not a real one), the same one this codebase's own
# masking tests use.
_VISA = "4111111111111111"

LOGIN_KEY = "test-login-key-0123456789"
SIGNING_KEY = "s" * 40


def _redirect_root_handler_to(buffer: io.StringIO) -> None:
    """Point every ``StreamHandler`` on the root logger at ``buffer`` instead of its own stream.

    ``configure_logging`` installs exactly one; this runs after it, so tests read log output back
    deterministically regardless of what ``sys.stderr`` happens to be at that point.
    """
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.stream = buffer


def _json_lines(buffer: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buffer.getvalue().strip().splitlines() if line.strip()]


@pytest.fixture
def log_buffer() -> io.StringIO:
    """Configures logging fresh for each test and returns the buffer its output lands in."""
    configure_logging("INFO", service_version="test-sha", environment="local")
    buffer = io.StringIO()
    _redirect_root_handler_to(buffer)
    return buffer


def test_a_log_line_is_valid_json_with_the_full_base_schema(log_buffer: io.StringIO) -> None:
    logger = logging.getLogger("tests.observability")

    logger.info("something_happened key=value")

    payload = _json_lines(log_buffer)[-1]
    assert payload["event"] == "something_happened"
    assert payload["message"] == "something_happened key=value"
    assert payload["level"] == "info"
    assert payload["service"] == "dispute-intake"
    assert payload["service_version"] == "test-sha"
    assert payload["environment"] == "local"
    assert payload["component"] == "tests.observability"
    assert isinstance(payload["timestamp"], str) and payload["timestamp"]
    assert payload["trace_id"] == "-"
    assert payload["session_id"] is None


def test_a_card_shaped_digit_run_is_redacted(log_buffer: io.StringIO) -> None:
    logger = logging.getLogger("tests.observability")

    logger.info("customer_message_received text=%s", _VISA)

    payload = _json_lines(log_buffer)[-1]
    assert _VISA not in str(payload["message"])
    assert "[card-number-redacted]" in str(payload["message"])


def test_an_error_carries_the_exception_text(log_buffer: io.StringIO) -> None:
    logger = logging.getLogger("tests.observability")

    try:
        raise ValueError("boom")
    except ValueError:
        logger.error("unhandled_error request_id=abc", exc_info=True)

    payload = _json_lines(log_buffer)[-1]
    assert payload["level"] == "error"
    assert "ValueError: boom" in str(payload["exc_info"])


def test_an_error_lines_shape_matches_a_cloudwatch_metric_filter_pattern(
    log_buffer: io.StringIO,
) -> None:
    """Log-based error alarms: proves (rather than redesigns) that an ERROR line's own
    shape is already what a CloudWatch metric filter pattern like ``{ $.level = "error" }`` would
    match — a plain top-level JSON key, never nested or renamed, and never absent on a WARNING or
    INFO line (a filter this loose-jointed would otherwise over- or under-count)."""
    logger = logging.getLogger("tests.observability")

    logger.warning("something_degraded")
    logger.info("something_happened")
    logger.error("something_failed")

    payloads = _json_lines(log_buffer)
    assert [p["level"] for p in payloads] == ["warning", "info", "error"]
    assert sum(1 for p in payloads if p["level"] == "error") == 1


def test_configure_logging_replaces_rather_than_stacks_handlers(log_buffer: io.StringIO) -> None:
    configure_logging("INFO", service_version="test-sha", environment="local")
    _redirect_root_handler_to(log_buffer)
    logger = logging.getLogger("tests.observability")

    logger.info("logged_once")

    assert len(_json_lines(log_buffer)) == 1


def test_a_level_below_the_configured_floor_is_not_emitted() -> None:
    configure_logging("WARNING", service_version="test-sha", environment="local")
    buffer = io.StringIO()
    _redirect_root_handler_to(buffer)
    logger = logging.getLogger("tests.observability")

    logger.info("should_not_appear")
    logger.warning("should_appear")

    output = buffer.getvalue()
    assert "should_not_appear" not in output
    assert "should_appear" in output


# ---------------------------------------------------------------------------
# End-to-end: trace_id and session_id flow from a real request into a log line.
# ---------------------------------------------------------------------------


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "session_signing_key": SecretStr(SIGNING_KEY),
        "test_identity_enabled": True,
        "test_identity_key": SecretStr(LOGIN_KEY),
        "service_version": "test-sha",
        "data_as_of_date": "2026-06-18",
    }
    return load_settings(env_file=None).model_copy(update={**values, **updates})


def _always_active(customer_id: str) -> str | None:
    return "Active"


@pytest.fixture
def app() -> FastAPI:
    instance = create_app(_settings(), customer_lookup=_always_active)

    @instance.get("/v1/log-something")
    def log_something() -> dict[str, str]:
        logging.getLogger("tests.observability").info("test_route_hit")
        return {"status": "ok"}

    return instance


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Redirects the handler ``create_app`` installed only after the app (and its own call to
    ``configure_logging``) already exists."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def app_log_buffer(app: FastAPI) -> io.StringIO:
    buffer = io.StringIO()
    _redirect_root_handler_to(buffer)
    return buffer


def test_an_authenticated_request_logs_its_own_trace_id_and_session_id(
    client: TestClient, app_log_buffer: io.StringIO
) -> None:
    login = client.post(
        "/v1/auth/test-sessions",
        json={"customer_id": "CUST-1"},
        headers={"X-Test-Login-Key": LOGIN_KEY},
    )
    token = login.json()["access_token"]

    response = client.get(
        "/v1/log-something",
        headers={"Authorization": f"Bearer {token}", "X-Request-ID": "r-abc-01234"},
    )

    assert response.status_code == 200
    route_lines = [
        line for line in _json_lines(app_log_buffer) if line["event"] == "test_route_hit"
    ]
    assert len(route_lines) == 1
    assert route_lines[0]["trace_id"] == "r-abc-01234"
    session_response = client.get(
        "/v1/session", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert route_lines[0]["session_id"] == session_response["session_id"]


def test_an_unauthenticated_public_request_logs_no_session_id(
    client: TestClient, app_log_buffer: io.StringIO
) -> None:
    """The sandbox login path itself is public: the request that mints a session is not itself
    authenticated, so its own log line carries no session_id."""
    login = client.post(
        "/v1/auth/test-sessions",
        json={"customer_id": "CUST-1"},
        headers={"X-Test-Login-Key": LOGIN_KEY},
    )

    assert login.status_code == 201
    login_lines = [
        line for line in _json_lines(app_log_buffer) if line["event"] == "session_issued"
    ]
    assert len(login_lines) == 1
    assert login_lines[0]["session_id"] is None
