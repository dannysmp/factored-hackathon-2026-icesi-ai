"""
Bounded Retry Tests
====================

Component: ``app.reliability.retry``. Hermetic: the sleep between attempts and the circuit
breaker are both fakes; no real delay, no real dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.llm.client import (
    CompletionRequest,
    CompletionResult,
    LlmClient,
    LlmError,
    LlmOutputInvalid,
    LlmRequestRejected,
    LlmUnavailable,
    ToolSpec,
)
from app.reliability.retry import RetriedLlmClient, RetryPolicy, backoff_seconds

_TOOL = ToolSpec(name="t", description="d", input_schema={})
_REQUEST = CompletionRequest(
    model="claude-haiku-4-5-20251001", system="s", user_text="u", tool=_TOOL, prompt_version="1"
)


def test_backoff_never_exceeds_the_max_delay() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay_ms=1000, max_delay_ms=2000)

    for attempt in range(1, 10):
        assert 0 <= backoff_seconds(policy, attempt) <= 2.0


def test_backoff_caps_grow_exponentially_before_the_max_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix ``random.uniform`` to always return its own upper bound, so the cap itself — not the
    jittered draw — is what this test observes."""
    monkeypatch.setattr("app.reliability.retry.random.uniform", lambda _low, high: high)
    policy = RetryPolicy(max_attempts=5, base_delay_ms=100, max_delay_ms=100_000)

    assert backoff_seconds(policy, 1) == 100 / 1000
    assert backoff_seconds(policy, 2) == 200 / 1000
    assert backoff_seconds(policy, 3) == 400 / 1000


def test_backoff_caps_stop_growing_once_the_max_delay_is_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.reliability.retry.random.uniform", lambda _low, high: high)
    policy = RetryPolicy(max_attempts=10, base_delay_ms=100, max_delay_ms=300)

    assert backoff_seconds(policy, 3) == 300 / 1000  # cap would be 400, clamped to 300
    assert backoff_seconds(policy, 8) == 300 / 1000  # far beyond the cap, still clamped


@dataclass
class _FakeBreaker:
    allowed: bool = True
    successes: int = 0
    failures: int = 0

    def allow(self) -> bool:
        return self.allowed

    def record_success(self) -> None:
        self.successes += 1

    def record_failure(self) -> None:
        self.failures += 1


@dataclass
class _ScriptedLlm:
    """Raises or returns exactly what was queued, in order."""

    responses: list[LlmError | CompletionResult] = field(default_factory=list)
    calls: int = 0

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.calls += 1
        outcome = self.responses[self.calls - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


_RESULT = CompletionResult(
    tool_input={}, model="m", prompt_version="1", input_tokens=1, output_tokens=1, latency_ms=1.0
)
_SLEEPS: list[float] = []


def _no_sleep(seconds: float) -> None:
    _SLEEPS.append(seconds)


@pytest.fixture(autouse=True)
def _reset_sleeps() -> None:
    _SLEEPS.clear()


def _client(inner: _ScriptedLlm, *, breaker: _FakeBreaker, max_attempts: int) -> RetriedLlmClient:
    policy = RetryPolicy(max_attempts=max_attempts, base_delay_ms=10, max_delay_ms=20)
    return RetriedLlmClient(
        build_inner=lambda: inner, policy=policy, breaker=breaker, sleep=_no_sleep
    )


def test_a_successful_first_attempt_records_success_and_makes_one_call() -> None:
    inner = _ScriptedLlm(responses=[_RESULT])
    breaker = _FakeBreaker()
    client: LlmClient = _client(inner, breaker=breaker, max_attempts=3)

    result = client.complete(_REQUEST)

    assert result is _RESULT
    assert inner.calls == 1
    assert breaker.successes == 1
    assert breaker.failures == 0
    assert _SLEEPS == []


def test_a_transient_failure_is_retried_and_then_succeeds() -> None:
    inner = _ScriptedLlm(responses=[LlmUnavailable("timeout"), _RESULT])
    breaker = _FakeBreaker()
    client: LlmClient = _client(inner, breaker=breaker, max_attempts=3)

    result = client.complete(_REQUEST)

    assert result is _RESULT
    assert inner.calls == 2
    assert breaker.successes == 1
    assert breaker.failures == 0
    assert len(_SLEEPS) == 1


def test_exhausting_every_attempt_raises_and_records_a_failure() -> None:
    inner = _ScriptedLlm(responses=[LlmUnavailable("a"), LlmUnavailable("b"), LlmUnavailable("c")])
    breaker = _FakeBreaker()
    client: LlmClient = _client(inner, breaker=breaker, max_attempts=3)

    with pytest.raises(LlmUnavailable):
        client.complete(_REQUEST)

    assert inner.calls == 3
    assert breaker.failures == 1
    assert breaker.successes == 0
    assert len(_SLEEPS) == 2  # a sleep before each retry, none after the last failed attempt


@pytest.mark.parametrize("error", [LlmRequestRejected("bad key"), LlmOutputInvalid("bad json")])
def test_a_permanent_failure_is_never_retried_or_recorded_on_the_breaker(
    error: LlmError,
) -> None:
    inner = _ScriptedLlm(responses=[error])
    breaker = _FakeBreaker()
    client: LlmClient = _client(inner, breaker=breaker, max_attempts=3)

    with pytest.raises(type(error)):
        client.complete(_REQUEST)

    assert inner.calls == 1
    assert breaker.successes == 0
    assert breaker.failures == 0
    assert _SLEEPS == []


def test_an_open_breaker_refuses_before_any_attempt() -> None:
    inner = _ScriptedLlm(responses=[_RESULT])
    breaker = _FakeBreaker(allowed=False)
    client: LlmClient = _client(inner, breaker=breaker, max_attempts=3)

    with pytest.raises(LlmUnavailable):
        client.complete(_REQUEST)

    assert inner.calls == 0
    assert breaker.successes == 0
    assert breaker.failures == 0


def test_the_inner_client_is_built_lazily_and_only_once() -> None:
    inner = _ScriptedLlm(responses=[_RESULT, _RESULT])
    builds = 0

    def build_inner() -> LlmClient:
        nonlocal builds
        builds += 1
        return inner

    client = RetriedLlmClient(
        build_inner=build_inner,
        policy=RetryPolicy(max_attempts=1, base_delay_ms=1, max_delay_ms=1),
        breaker=_FakeBreaker(),
        sleep=_no_sleep,
    )
    assert builds == 0

    client.complete(_REQUEST)
    client.complete(_REQUEST)

    assert builds == 1
