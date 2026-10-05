"""
Tool Port Decorator Tests
==========================

Component: ``app.main._controller_factory`` and ``app.main.create_app``. Hermetic: no network, no
database, no ``.env``. Persistence collaborators are replaced by inert stand-ins, so the tests see
only how the optional ``tool_port_decorator`` is applied to each request's tool port.
"""

from __future__ import annotations

# Standard libraries
from datetime import UTC, date, datetime
from typing import Any, cast

# Third-party libraries
import pytest  # Fixtures and monkeypatch

# Local modules
import app.main as main_module
from app.config import load_settings
from app.domain.calendar import DateOrigin, DomainCalendar
from app.domain.policy.loader import load_policy
from app.reliability.tool_port import RetriedToolPort
from app.retrieval.lexical import LexicalRetriever
from app.security.sessions import Principal
from contracts.service_v1.tools import ToolPort

_NOW = datetime(2026, 6, 18, 17, 0, tzinfo=UTC)
_PRINCIPAL = Principal("c-1", "s-1", _NOW, _NOW, "dispute-intake")


class _Anywhere:
    """A persistence collaborator that accepts any construction and is never used."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None: ...

    def get(self, _session_id: str) -> None:
        return None


def _factory(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> Any:
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@localhost/unused")
    for name in (
        "PostgresDialogueStore",
        "PostgresAuditSink",
        "PostgresToolPort",
        "PostgresHandoffOutbox",
        "PostgresSpendLedger",
    ):
        monkeypatch.setattr(main_module, name, _Anywhere)
    return main_module._controller_factory(
        load_settings(env_file=None),
        policy=load_policy(),
        retriever=LexicalRetriever.from_corpus(),
        calendar=DomainCalendar(date(2026, 6, 18), DateOrigin.SETTING),
        clock=lambda: _NOW,
        **kwargs,
    )


def test_each_requests_tool_port_is_the_decorators_result_over_the_retried_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[Principal, ToolPort]] = []
    replacement = cast(ToolPort, object())

    def decorator(principal: Principal, port: ToolPort) -> ToolPort:
        seen.append((principal, port))
        return replacement

    controller = _factory(monkeypatch, tool_port_decorator=decorator)(_PRINCIPAL)

    assert controller._tool_port is replacement
    assert len(seen) == 1
    assert seen[0][0] == _PRINCIPAL
    assert isinstance(seen[0][1], RetriedToolPort)


def test_without_a_decorator_the_tool_port_is_the_retried_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _factory(monkeypatch)(_PRINCIPAL)

    assert isinstance(controller._tool_port, RetriedToolPort)


def test_create_app_hands_the_decorator_to_the_controller_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, Any] = {}

    def fake_factory(*_args: Any, **kwargs: Any) -> Any:
        received.update(kwargs)
        return lambda principal: None

    def decorator(principal: Principal, port: ToolPort) -> ToolPort:
        return port

    monkeypatch.setattr(main_module, "_controller_factory", fake_factory)
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("DATA_AS_OF_DATE", "2026-06-18")

    main_module.create_app(load_settings(env_file=None), tool_port_decorator=decorator)

    assert received["tool_port_decorator"] is decorator
