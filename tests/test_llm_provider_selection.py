"""
LLM Provider Selection Tests
=============================

Component: ``app.main._understanding`` and ``app.main._model_renderer``. Hermetic: no network,
no database, no ``.env``. Both take the shared ``llm_client`` ``_controller_factory`` builds
once; every case here either never touches it (the stub branch) or raises before it would
be touched, so a placeholder stands in for it without needing a real or fake ``LlmClient``.
"""

from __future__ import annotations

# Standard libraries
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

# Third-party libraries
import pytest  # Fixtures and monkeypatch

# Local modules
import app.main as main_module
from app.config import ConfigError, load_settings
from app.conversation.llm_understanding import LlmNlu
from app.conversation.model_renderer import LlmRenderer
from app.conversation.understanding import FakeNlu
from app.domain.calendar import DateOrigin, DomainCalendar
from app.domain.policy.loader import load_policy
from app.llm.client import CompletionRequest, CompletionResult, LlmClient, LlmUnavailable, ToolSpec
from app.llm.spend import LlmSpendLimitReached, SpendGatedLlmClient
from app.reliability.retry import RetriedLlmClient
from app.retrieval.lexical import LexicalRetriever
from app.security.sessions import Principal

_UNUSED_LLM_CLIENT = cast(LlmClient, object())


def test_the_stub_provider_builds_a_fake_understanding_with_no_key_and_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stub provider needs no ``ANTHROPIC_API_KEY`` at all."""
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    settings = load_settings(env_file=None)

    understanding = main_module._understanding(_UNUSED_LLM_CLIENT, settings)

    assert isinstance(understanding, FakeNlu)


def test_a_provider_with_no_adapter_still_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stub branch is additive: an unimplemented real provider still raises as before."""
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    settings = load_settings(env_file=None)

    with pytest.raises(ConfigError, match="'bedrock' LLM provider has no adapter yet"):
        main_module._understanding(_UNUSED_LLM_CLIENT, settings)


def test_model_rendering_under_the_stub_provider_fails_closed_not_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_model_renderer`` has no stub branch: it still requires the Anthropic provider, so
    enabling model rendering with ``LLM_PROVIDER=stub`` refuses loudly rather than doing nothing
    or reaching a provider it was never told to call."""
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("MODEL_RENDERER_ENABLED", "true")
    settings = load_settings(env_file=None)

    with pytest.raises(ConfigError, match="'stub' LLM provider has no adapter yet"):
        main_module._model_renderer(_UNUSED_LLM_CLIENT, settings)


class _Anywhere:
    """A persistence collaborator that accepts any construction and is never used."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None: ...

    def get(self, _session_id: str) -> None:
        return None


class _LedgerAtTheLimit:
    def __init__(self, _dsn: str) -> None: ...

    def spent(self, _day: date) -> Decimal:
        return Decimal("10")

    def add(self, _day: date, _usd: Decimal) -> None:
        raise AssertionError("a refused call must not be charged")


class _CountingModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.calls += 1
        raise AssertionError("the spend gate must stop the call before it reaches the model")


def test_both_model_paths_are_built_on_the_spend_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Understanding and rendering share one gated client that wraps the shared retried client,
    so a total at the limit stops the call before the provider is reached."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("MODEL_RENDERER_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@localhost/unused")
    settings = load_settings(env_file=None)
    provider = _CountingModel()
    for name in (
        "PostgresDialogueStore",
        "PostgresAuditSink",
        "PostgresToolPort",
        "PostgresHandoffOutbox",
    ):
        monkeypatch.setattr(main_module, name, _Anywhere)
    monkeypatch.setattr(main_module, "PostgresSpendLedger", _LedgerAtTheLimit)
    monkeypatch.setattr(main_module, "_build_anthropic_client", lambda _settings: provider)
    now = datetime(2026, 6, 18, 17, 0, tzinfo=UTC)
    factory = main_module._controller_factory(
        settings,
        policy=load_policy(),
        retriever=LexicalRetriever.from_corpus(),
        calendar=DomainCalendar(date(2026, 6, 18), DateOrigin.SETTING),
        clock=lambda: now,
    )
    principal = Principal("c-1", "s-1", now, now, "dispute-intake")

    controller = factory(principal)

    nlu = controller._understanding
    renderer = controller._model_renderer
    assert isinstance(nlu, LlmNlu)
    assert isinstance(renderer, LlmRenderer)
    gated = renderer._llm
    assert isinstance(gated, SpendGatedLlmClient)
    assert nlu._llm is gated
    assert gated.limit_usd == Decimal("10")
    assert isinstance(gated.inner, RetriedLlmClient)
    tool = ToolSpec(name="t", description="d", input_schema={})
    request = CompletionRequest(
        model=settings.nlu_model, system="s", user_text="u", tool=tool, prompt_version="1"
    )
    with pytest.raises(LlmSpendLimitReached):
        gated.complete(request)
    assert provider.calls == 0
    assert issubclass(LlmSpendLimitReached, LlmUnavailable)
