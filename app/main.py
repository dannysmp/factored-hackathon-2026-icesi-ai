"""
Service Composition Root
========================

Overview
--------
Builds the FastAPI application. Configuration is loaded here — and only here — so importing
the module is free of side effects and tests can inject their own settings.

Scope
-----
In: application factory and health endpoints.
Out: business routes, middleware and tools.

Design Principles
-----------------
- ``create_app`` is a factory: ``uvicorn app.main:create_app --factory``.
- Liveness is dependency-free; readiness reports version information and will gate on
  dependencies as they are introduced.

Runtime Contract
----------------
``GET /health/live``  -> ``{"status": "live"}``
``GET /health/ready`` -> ``{"status": "ready", "service_version": str, "environment": str}``

Limitations
-----------
Request logging, tracing and error mapping are not implemented yet.
"""

from __future__ import annotations

# Third-party libraries
from fastapi import FastAPI  # Web framework

# Local modules
from app.config import (
    Settings,  # Validated configuration injected into the app
    load_settings,  # Loads configuration when none is injected
)

# -----------------------------------------------------------------------------
# Application factory
# -----------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Parameters
    ----------
    settings : Settings | None
        Configuration to use; loaded from the environment when omitted.

    Returns
    -------
    FastAPI
        Application with the health endpoints registered.

    Raises
    ------
    ConfigError
        When no settings are given and the environment is invalid.
    """
    # Resolve configuration once, failing fast before any route is registered
    resolved = settings if settings is not None else load_settings()
    app = FastAPI(title="Dispute Intake API", version=resolved.service_version)

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

    return app
