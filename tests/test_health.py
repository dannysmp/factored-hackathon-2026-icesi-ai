"""
Health Endpoint Contract Tests
==============================

Component: ``app.main.create_app``. Hermetic: FastAPI's in-process test client, injected
settings, no network and no ``.env``.
"""

from __future__ import annotations

# Third-party libraries
import pytest  # Test runner and fixtures
from fastapi.testclient import TestClient  # In-process client for the app
from pydantic import SecretStr  # DSN values, which the settings type requires

# Local modules
import app.main as main_module
from app.config import ConfigError, LlmProvider, load_settings
from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    """App built from injected settings so no environment is consulted."""
    settings = load_settings(env_file=None).model_copy(
        update={"service_version": "test-sha", "data_as_of_date": "2026-06-18"}
    )
    return TestClient(create_app(settings))


def test_liveness_reports_live_without_dependencies(client: TestClient) -> None:
    """Liveness is a constant, dependency-free answer."""
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_readiness_reports_version_and_environment(client: TestClient) -> None:
    """Readiness exposes the build identifier, environment and the domain date."""
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service_version": "test-sha",
        "environment": "local",
        "domain_date": "2026-06-18",
        "domain_date_origin": "setting",
    }


def test_readiness_reports_the_system_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The literal "system" setting is distinguishable from an explicit date in the response."""
    monkeypatch.setenv("DATA_AS_OF_DATE", "system")
    settings = load_settings(env_file=None).model_copy(update={"service_version": "test-sha"})

    response = TestClient(create_app(settings)).get("/health/ready")

    assert response.json()["domain_date_origin"] == "system"


def test_factory_fails_fast_on_invalid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without injected settings the factory validates the environment before serving."""
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        create_app()


def test_a_configuration_failure_emits_a_critical_event_naming_the_setting_not_its_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No structured logging is installed yet at this point; the factory must install a fallback
    logger before the failure, or an operator sees an unstructured traceback and no queryable
    event. ``caplog`` cannot observe this: ``configure_logging`` replaces the root logger's
    handlers, including pytest's own, so the emitted line is asserted on the stream it is actually
    written to instead."""
    monkeypatch.setenv("LOG_LEVEL", "not-a-real-level")

    with pytest.raises(ConfigError):
        create_app()

    lines = [
        line for line in capsys.readouterr().err.splitlines() if '"event": "config_invalid"' in line
    ]
    assert len(lines) == 1
    assert '"level": "critical"' in lines[0]
    assert "LOG_LEVEL" in lines[0]
    assert "not-a-real-level" not in lines[0]


def test_factory_refuses_to_start_with_no_domain_date_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No DATA_AS_OF_DATE and no configured store: the service must not guess the real date."""
    monkeypatch.delenv("DATA_AS_OF_DATE", raising=False)
    settings = load_settings(env_file=None)

    with pytest.raises(ConfigError, match="No domain date resolves"):
        create_app(settings)


def test_building_the_anthropic_client_refuses_an_unadapted_provider() -> None:
    """Bedrock has no adapter yet: building the client fails closed with a clear message,
    matching every other "no adapter yet" refusal in this codebase."""
    settings = load_settings(env_file=None).model_copy(update={"llm_provider": LlmProvider.BEDROCK})

    with pytest.raises(ConfigError, match="bedrock"):
        main_module._build_anthropic_client(settings)


def test_factory_refuses_to_start_when_the_seed_has_no_reference_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured store that cannot answer is the same as no store: refuse, do not guess."""
    monkeypatch.delenv("DATA_AS_OF_DATE", raising=False)
    monkeypatch.setattr(main_module, "read_data_as_of", lambda _dsn: None)
    settings = load_settings(env_file=None).model_copy(
        update={"database_url": SecretStr("postgresql://configured-but-empty/db")}
    )

    with pytest.raises(ConfigError, match="No domain date resolves"):
        create_app(settings)


def test_the_seed_is_read_only_when_the_setting_does_not_already_settle_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment that overrides the date never needs the database up at start-up."""
    seen: list[str] = []

    def fake_read_data_as_of(dsn: str) -> None:
        seen.append(dsn)

    monkeypatch.setattr(main_module, "read_data_as_of", fake_read_data_as_of)
    settings = load_settings(env_file=None).model_copy(
        update={
            "data_as_of_date": "2026-06-18",
            "database_url": SecretStr("postgresql://never-read/db"),
        }
    )

    create_app(settings)

    assert seen == []
