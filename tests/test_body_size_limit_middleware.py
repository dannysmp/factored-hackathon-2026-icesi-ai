"""
Body-Size-Limit Middleware Tests
=================================

Component: ``app.security.middleware.BodySizeLimitMiddleware``. Hermetic: a minimal ASGI app and
FastAPI's in-process test client; no database, no LLM call.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Mapping

# Third-party libraries
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

# Local modules
from app.security.errors import ErrorCode
from app.security.middleware import BodySizeLimitMiddleware, RequestContextMiddleware


async def _echo_length(request: Request) -> PlainTextResponse:
    body = await request.body()
    return PlainTextResponse(str(len(body)))


def _app(max_bytes: int) -> Starlette:
    app = Starlette(routes=[Route("/echo", _echo_length, methods=["POST"])])
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_bytes)
    app.add_middleware(RequestContextMiddleware)
    return app


def test_a_body_within_the_limit_reaches_the_application() -> None:
    client = TestClient(_app(max_bytes=16))

    response = client.post("/echo", content=b"twelve bytes")

    assert response.status_code == 200
    assert response.text == "12"


def test_a_body_over_the_limit_is_refused_before_the_application_runs() -> None:
    client = TestClient(_app(max_bytes=16))

    response = client.post("/echo", content=b"this body is much too large for the limit")

    assert response.status_code == 413
    body: Mapping[str, object] = response.json()
    assert body["code"] == ErrorCode.PAYLOAD_TOO_LARGE.value
    assert "X-Request-ID" in response.headers


def test_the_limit_counts_bytes_actually_read_not_content_length() -> None:
    """A missing or understated Content-Length never lets an oversized body through."""
    client = TestClient(_app(max_bytes=16))

    response = client.post(
        "/echo",
        content=b"this body is much too large for the limit",
        headers={"Content-Length": "5"},
    )

    assert response.status_code == 413
