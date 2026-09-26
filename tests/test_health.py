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

# Local modules
from app.config import ConfigError, load_settings
from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    """App built from injected settings so no environment is consulted."""
    settings = load_settings(env_file=None).model_copy(update={"service_version": "test-sha"})
    return TestClient(create_app(settings))


def test_liveness_reports_live_without_dependencies(client: TestClient) -> None:
    """Liveness is a constant, dependency-free answer."""
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_readiness_reports_version_and_environment(client: TestClient) -> None:
    """Readiness exposes the build identifier and environment for smoke tests."""
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service_version": "test-sha",
        "environment": "local",
    }


def test_factory_fails_fast_on_invalid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without injected settings the factory validates the environment before serving."""
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        create_app()
