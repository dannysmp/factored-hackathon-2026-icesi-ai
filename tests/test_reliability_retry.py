"""
Bounded Retry Tests
====================

Component: ``app.reliability.retry``. Hermetic: the sleep between attempts and (except where a
test says otherwise) the circuit breaker are both fakes; no real delay, no real dependency.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

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
from app.reliability.breaker import InMemoryCircuitBreaker
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

    responses: list[LlmError | CompletionResult | Exception] = field(default_factory=list)
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
def test_a_permanent_failure_is_never_retried_but_still_records_a_success(
    error: LlmError,
) -> None:
    """The provider answered — a rejection or unparsable output is not an outage — so the breaker
    sees a reachability success, even though the caller still sees the original error raised."""
    inner = _ScriptedLlm(responses=[error])
    breaker = _FakeBreaker()
    client: LlmClient = _client(inner, breaker=breaker, max_attempts=3)

    with pytest.raises(type(error)):
        client.complete(_REQUEST)

    assert inner.calls == 1
    assert breaker.successes == 1
    assert breaker.failures == 0
    assert _SLEEPS == []


def test_an_exception_outside_the_llmerror_contract_records_a_failure_and_propagates() -> None:
    """Defensive backstop: the inner client's own contract says it never raises outside
    ``LlmError``, but if a bug or a future implementation ever did, that must still resolve the
    breaker before propagating, exactly like any other outcome, and is not itself retried."""
    error = RuntimeError("unexpected bug in the inner client")
    inner = _ScriptedLlm(responses=[error])
    breaker = _FakeBreaker()
    client: LlmClient = _client(inner, breaker=breaker, max_attempts=3)

    with pytest.raises(RuntimeError):
        client.complete(_REQUEST)

    assert inner.calls == 1
    assert breaker.failures == 1
    assert breaker.successes == 0
    assert _SLEEPS == []


def test_a_half_open_trial_that_raises_an_unexpected_exception_reopens_but_does_not_wedge() -> None:
    """Regression for a real bug: a half-open breaker allows exactly one trial call and then
    refuses everything until that trial's own outcome is recorded. A genuine failure correctly
    reopens the breaker for a fresh, bounded window — the bug this guards against is a PERMANENT
    refusal that no amount of waiting would ever clear, not this normal, bounded reopening."""
    start = datetime(2026, 1, 1)
    clock = {"now": start}
    breaker = InMemoryCircuitBreaker(1, 30, clock=lambda: clock["now"])
    breaker.record_failure()  # opens the breaker
    clock["now"] = start + timedelta(seconds=30)  # the reset window has now elapsed

    inner = _ScriptedLlm(responses=[RuntimeError("unexpected bug in the inner client")])
    client: LlmClient = RetriedLlmClient(
        build_inner=lambda: inner,
        policy=RetryPolicy(max_attempts=3, base_delay_ms=1, max_delay_ms=1),
        breaker=breaker,
        sleep=_no_sleep,
    )

    # This call's own allow() consumes the one half-open trial.
    with pytest.raises(RuntimeError):
        client.complete(_REQUEST)

    # Correctly reopened, not wedged: refused now, but only for a fresh, bounded window.
    assert not breaker.allow()
    clock["now"] = clock["now"] + timedelta(seconds=30)
    assert breaker.allow()


@pytest.mark.parametrize("error", [LlmRequestRejected("bad key"), LlmOutputInvalid("bad json")])
def test_a_half_open_trial_that_hits_a_permanent_failure_does_not_stay_wedged(
    error: LlmError,
) -> None:
    """Regression for a real bug: a half-open breaker allows exactly one trial call and then
    refuses everything until that trial's own outcome is recorded. Composing the REAL breaker
    (not the fake above) with the real client proves that a trial resolving to a permanent LLM
    failure still resolves the breaker, rather than leaving it permanently half-open."""
    start = datetime(2026, 1, 1)
    clock = {"now": start}
    breaker = InMemoryCircuitBreaker(1, 30, clock=lambda: clock["now"])
    breaker.record_failure()  # opens the breaker
    clock["now"] = start + timedelta(seconds=30)  # the reset window has now elapsed

    inner = _ScriptedLlm(responses=[error])
    client: LlmClient = RetriedLlmClient(
        build_inner=lambda: inner,
        policy=RetryPolicy(max_attempts=3, base_delay_ms=1, max_delay_ms=1),
        breaker=breaker,
        sleep=_no_sleep,
    )

    # This call's own allow() consumes the one half-open trial.
    with pytest.raises(type(error)):
        client.complete(_REQUEST)

    # If the trial's outcome had never been recorded, this would still refuse forever.
    assert breaker.allow()


def test_retrying_logs_a_warning_for_each_attempt(caplog: pytest.LogCaptureFixture) -> None:
    inner = _ScriptedLlm(responses=[LlmUnavailable("a"), LlmUnavailable("b"), _RESULT])
    client: LlmClient = _client(inner, breaker=_FakeBreaker(), max_attempts=3)

    with caplog.at_level(logging.WARNING, logger="app.reliability.retry"):
        client.complete(_REQUEST)

    messages = [r.message for r in caplog.records if r.name == "app.reliability.retry"]
    assert len(messages) == 2
    assert "llm_retry_attempt attempt=1 max_attempts=3" in messages[0]
    assert "llm_retry_attempt attempt=2 max_attempts=3" in messages[1]


def test_the_inner_client_build_is_guarded_against_a_concurrent_race() -> None:
    """The turns route is a sync handler FastAPI dispatches on its thread pool, so two requests'
    first calls can race the lazy build; the lock must let exactly one build through."""
    builds = 0
    build_lock = threading.Lock()
    start = threading.Barrier(8)

    def build_inner() -> LlmClient:
        nonlocal builds
        time.sleep(0.01)  # widen the race window
        with build_lock:
            builds += 1
        return _ScriptedLlm(responses=[_RESULT])

    client = RetriedLlmClient(
        build_inner=build_inner,
        policy=RetryPolicy(max_attempts=1, base_delay_ms=1, max_delay_ms=1),
        breaker=_FakeBreaker(),
        sleep=_no_sleep,
    )

    def call() -> None:
        start.wait()
        client._inner_client()

    threads = [threading.Thread(target=call) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert builds == 1


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
