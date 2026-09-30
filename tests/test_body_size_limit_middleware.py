"""
Body-Size-Limit Middleware Tests
=================================

Component: ``app.security.middleware.BodySizeLimitMiddleware``. Hermetic: a minimal ASGI app and
FastAPI's in-process test client; no database, no LLM call.
"""

from __future__ import annotations

# Standard libraries
import asyncio
import json
from collections.abc import Mapping, MutableMapping
from typing import Any

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


def test_a_body_split_across_several_asgi_messages_is_replayed_whole() -> None:
    """A body arriving as more than one ``http.request`` message (``more_body=True`` between
    them) is fully reassembled for the downstream app, not just its first chunk."""
    chunks = [b"first-", b"second-", b"third"]
    received: list[bytes] = []

    async def downstream_app(scope: Any, receive: Any, send: Any) -> None:
        body = b""
        more = True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)
        received.append(body)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = BodySizeLimitMiddleware(downstream_app, max_bytes=1024)

    async def fake_receive() -> MutableMapping[str, Any]:
        if not chunks:
            return {"type": "http.request", "body": b"", "more_body": False}
        chunk = chunks.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

    sent: list[MutableMapping[str, Any]] = []

    async def fake_send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope: MutableMapping[str, Any] = {
        "type": "http",
        "path": "/echo",
        "headers": [],
        "method": "POST",
    }
    asyncio.run(middleware(scope, fake_receive, fake_send))

    assert received == [b"first-second-third"]
    assert sent[0]["status"] == 200


def test_a_stalled_read_is_refused_with_a_request_timeout() -> None:
    """A client that never delivers its next chunk (slow-loris style) is refused once the read
    stalls past the timeout, rather than holding the loop open indefinitely."""

    async def stalling_receive() -> MutableMapping[str, Any]:
        await asyncio.sleep(10)  # Far longer than the middleware's own short timeout below
        return {"type": "http.request", "body": b"", "more_body": False}  # pragma: no cover

    async def downstream_app(scope: Any, receive: Any, send: Any) -> None:
        raise AssertionError("the downstream app must never run for a stalled read")

    middleware = BodySizeLimitMiddleware(downstream_app, max_bytes=1024, read_timeout_seconds=0.05)

    sent: list[MutableMapping[str, Any]] = []

    async def fake_send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope: MutableMapping[str, Any] = {
        "type": "http",
        "path": "/echo",
        "headers": [],
        "method": "POST",
        "state": {},
    }
    asyncio.run(middleware(scope, stalling_receive, fake_send))

    assert sent[0]["status"] == 408
    body = json.loads(b"".join(m["body"] for m in sent if m["type"] == "http.response.body"))
    assert body["code"] == ErrorCode.REQUEST_TIMEOUT.value


def test_a_slowly_paced_trickle_is_refused_once_the_whole_drain_exceeds_the_deadline() -> None:
    """A client that never stalls any single read, but paces small chunks just inside a per-read
    window forever, is still refused once the whole drain takes too long — a per-read idle timeout
    alone would let this client reset the clock on every message and never be caught. Bounded to 5
    chunks so a regression here fails a downstream-app assertion quickly rather than hanging."""
    chunks_sent = 0

    async def trickling_receive() -> MutableMapping[str, Any]:
        nonlocal chunks_sent
        await asyncio.sleep(0.03)  # Comfortably inside any single-read window
        chunks_sent += 1
        return {"type": "http.request", "body": b"x", "more_body": chunks_sent < 5}

    async def downstream_app(scope: Any, receive: Any, send: Any) -> None:
        raise AssertionError("the downstream app must never run once the deadline is exceeded")

    middleware = BodySizeLimitMiddleware(
        downstream_app, max_bytes=1024 * 1024, read_timeout_seconds=0.1
    )

    sent: list[MutableMapping[str, Any]] = []

    async def fake_send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope: MutableMapping[str, Any] = {
        "type": "http",
        "path": "/echo",
        "headers": [],
        "method": "POST",
        "state": {},
    }
    asyncio.run(middleware(scope, trickling_receive, fake_send))

    assert sent[0]["status"] == 408
    assert chunks_sent >= 2  # more than one message was read before the overall deadline fired


def test_a_read_that_completes_within_the_timeout_is_not_refused() -> None:
    """A slower-than-instant, but still timely, chunk is not mistaken for a stall."""
    client = TestClient(_app(max_bytes=1024))

    response = client.post("/echo", content=b"on time")

    assert response.status_code == 200
