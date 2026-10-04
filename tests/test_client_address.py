"""
Client Address Tests
====================

Component: ``app.security.client_address``. Hermetic: requests are built from ASGI scopes.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.security.client_address import client_address


def _request(forwarded: str | None, host: str | None = "10.0.0.5") -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    scope = {
        "type": "http",
        "headers": headers,
        "client": (host, 1234) if host is not None else None,
    }
    return Request(scope)


def test_the_last_forwarded_entry_is_the_client_address() -> None:
    assert client_address(_request("198.51.100.1, 203.0.113.30")) == "203.0.113.30"


def test_a_single_forwarded_entry_is_the_client_address() -> None:
    assert client_address(_request("203.0.113.30")) == "203.0.113.30"


def test_without_a_forwarded_header_the_connecting_address_is_used() -> None:
    assert client_address(_request(None)) == "10.0.0.5"


@pytest.mark.parametrize("forwarded", ["198.51.100.1,", "198.51.100.1, ", ",", " ", ""])
def test_an_empty_last_entry_falls_back_to_the_connecting_address(forwarded: str) -> None:
    assert client_address(_request(forwarded)) == "10.0.0.5"


def test_without_any_client_information_the_address_is_unknown() -> None:
    assert client_address(_request(None, host=None)) == "unknown"
