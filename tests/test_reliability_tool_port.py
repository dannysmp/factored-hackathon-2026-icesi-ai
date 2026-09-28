"""
Bounded Retry for the Tool Port Tests
=======================================

Component: ``app.reliability.tool_port``. Hermetic: the sleep between attempts, the circuit
breaker and the wrapped ``ToolPort`` are all fakes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.reliability.breaker import CircuitBreaker, InMemoryCircuitBreaker
from app.reliability.retry import RetryPolicy
from app.reliability.tool_port import RetriedToolPort
from contracts.service_v1.tools import Tool, ToolFailure, TransactionFilters, TransactionPage

_FILTERS = TransactionFilters()
_PAGE = TransactionPage(items=(), total_count=0)
_SLEEPS: list[float] = []


def _no_sleep(seconds: float) -> None:
    _SLEEPS.append(seconds)


@pytest.fixture(autouse=True)
def _reset_sleeps() -> None:
    _SLEEPS.clear()


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
class _ScriptedPort:
    """Returns or raises exactly what was queued for ``list_transactions``, in order."""

    responses: list[TransactionPage | ToolFailure | Exception] = field(default_factory=list)
    calls: int = 0

    def list_transactions(self, filters: TransactionFilters) -> TransactionPage | ToolFailure:
        self.calls += 1
        outcome = self.responses[self.calls - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def get_transaction(self, ref: str) -> None:
        raise NotImplementedError

    def list_dispute_cases(self) -> tuple[()]:
        raise NotImplementedError

    def get_case(self, case_number: str) -> None:
        raise NotImplementedError

    def evaluate_dispute(self, request: object) -> None:
        raise NotImplementedError

    def create_dispute_case(self, request: object) -> None:
        raise NotImplementedError


def _port(inner: _ScriptedPort, *, breaker: CircuitBreaker, max_attempts: int) -> RetriedToolPort:
    policy = RetryPolicy(max_attempts=max_attempts, base_delay_ms=10, max_delay_ms=20)
    return RetriedToolPort(inner, policy=policy, breaker=breaker, sleep=_no_sleep)  # type: ignore[arg-type]


def test_a_successful_first_attempt_records_success_and_makes_one_call() -> None:
    inner = _ScriptedPort(responses=[_PAGE])
    breaker = _FakeBreaker()
    port = _port(inner, breaker=breaker, max_attempts=3)

    result = port.list_transactions(_FILTERS)

    assert result is _PAGE
    assert inner.calls == 1
    assert breaker.successes == 1
    assert breaker.failures == 0
    assert _SLEEPS == []


def test_a_retryable_failure_is_retried_and_then_succeeds() -> None:
    inner = _ScriptedPort(
        responses=[ToolFailure(tool=Tool.LIST_TRANSACTIONS, cause="error"), _PAGE]
    )
    breaker = _FakeBreaker()
    port = _port(inner, breaker=breaker, max_attempts=3)

    result = port.list_transactions(_FILTERS)

    assert result is _PAGE
    assert inner.calls == 2
    assert breaker.successes == 1
    assert breaker.failures == 0
    assert len(_SLEEPS) == 1


def test_exhausting_every_attempt_returns_the_failure_and_records_it() -> None:
    failure = ToolFailure(tool=Tool.LIST_TRANSACTIONS, cause="timeout")
    inner = _ScriptedPort(responses=[failure, failure, failure])
    breaker = _FakeBreaker()
    port = _port(inner, breaker=breaker, max_attempts=3)

    result = port.list_transactions(_FILTERS)

    assert result is failure
    assert inner.calls == 3
    assert breaker.failures == 1
    assert breaker.successes == 0
    assert len(_SLEEPS) == 2


def test_a_raised_exception_records_a_failure_and_propagates_without_retrying() -> None:
    """``PostgresToolPort`` raises directly (never returns a ``ToolFailure``) when its fail-closed
    audit write fails; that must still resolve the breaker, exactly like any other outcome, and is
    not itself retried — only the value-based ``ToolFailure`` signal is."""
    error = RuntimeError("audit write failed")
    inner = _ScriptedPort(responses=[error])
    breaker = _FakeBreaker()
    port = _port(inner, breaker=breaker, max_attempts=3)

    with pytest.raises(RuntimeError):
        port.list_transactions(_FILTERS)

    assert inner.calls == 1
    assert breaker.failures == 1
    assert breaker.successes == 0
    assert _SLEEPS == []


def test_a_half_open_trial_that_raises_reopens_but_does_not_wedge() -> None:
    """Regression for a real bug: a half-open breaker allows exactly one trial call and then
    refuses everything until that trial's own outcome is recorded. A genuine failure correctly
    reopens the breaker for a fresh, bounded window — the bug this guards against is a PERMANENT
    refusal that no amount of waiting would ever clear, not this normal, bounded reopening."""
    start = datetime(2026, 1, 1)
    clock = {"now": start}
    breaker = InMemoryCircuitBreaker(1, 30, clock=lambda: clock["now"])
    breaker.record_failure()  # opens the breaker
    clock["now"] = start + timedelta(seconds=30)  # the reset window has now elapsed

    inner = _ScriptedPort(responses=[RuntimeError("audit write failed")])
    port = _port(inner, breaker=breaker, max_attempts=3)

    # This call's own allow() consumes the one half-open trial.
    with pytest.raises(RuntimeError):
        port.list_transactions(_FILTERS)

    # Correctly reopened, not wedged: refused now, but only for a fresh, bounded window.
    assert not breaker.allow()
    clock["now"] = clock["now"] + timedelta(seconds=30)
    assert breaker.allow()


def test_a_non_retryable_failure_is_returned_immediately_and_still_records_a_success() -> None:
    """A permission or not-found refusal is a correctness outcome, not infrastructure noise — but
    the store was reached and answered, so it resolves the breaker like any other success."""
    failure = ToolFailure(tool=Tool.LIST_TRANSACTIONS, cause="error", retryable=False)
    inner = _ScriptedPort(responses=[failure])
    breaker = _FakeBreaker()
    port = _port(inner, breaker=breaker, max_attempts=3)

    result = port.list_transactions(_FILTERS)

    assert result is failure
    assert inner.calls == 1
    assert breaker.successes == 1
    assert breaker.failures == 0
    assert _SLEEPS == []


def test_a_circuit_open_failure_from_the_inner_port_is_never_retried_either() -> None:
    """Retrying the breaker's own signal would defeat it — even if the inner port somehow
    returned one directly, it is passed straight through, not retried, and still recorded as a
    success (the inner port did answer, it just echoed the same signal back)."""
    failure = ToolFailure(tool=Tool.LIST_TRANSACTIONS, cause="circuit_open")
    inner = _ScriptedPort(responses=[failure])
    breaker = _FakeBreaker()
    port = _port(inner, breaker=breaker, max_attempts=3)

    result = port.list_transactions(_FILTERS)

    assert result is failure
    assert inner.calls == 1
    assert breaker.successes == 1
    assert _SLEEPS == []


def test_a_half_open_trial_that_hits_a_non_retryable_failure_does_not_stay_wedged() -> None:
    """Regression for a real bug: a half-open breaker allows exactly one trial call and then
    refuses everything until that trial's own outcome is recorded. Composing the REAL breaker
    (not the fake above) proves that a trial resolving to a routine business refusal — not an
    outage — still resolves the breaker, rather than leaving it permanently half-open."""
    start = datetime(2026, 1, 1)
    clock = {"now": start}
    breaker = InMemoryCircuitBreaker(1, 30, clock=lambda: clock["now"])
    breaker.record_failure()  # opens the breaker
    clock["now"] = start + timedelta(seconds=30)  # the reset window has now elapsed

    failure = ToolFailure(tool=Tool.LIST_TRANSACTIONS, cause="error", retryable=False)
    inner = _ScriptedPort(responses=[failure])
    port = _port(inner, breaker=breaker, max_attempts=3)

    # This call's own allow() consumes the one half-open trial.
    result = port.list_transactions(_FILTERS)

    assert result is failure
    # If the trial's outcome had never been recorded, this would still refuse forever.
    assert breaker.allow()


def test_retrying_logs_a_warning_naming_the_tool_for_each_attempt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failure = ToolFailure(tool=Tool.LIST_TRANSACTIONS, cause="error")
    inner = _ScriptedPort(responses=[failure, failure, _PAGE])
    port = _port(inner, breaker=_FakeBreaker(), max_attempts=3)

    with caplog.at_level(logging.WARNING, logger="app.reliability.tool_port"):
        port.list_transactions(_FILTERS)

    messages = [r.message for r in caplog.records if r.name == "app.reliability.tool_port"]
    assert len(messages) == 2
    assert "tool_retry_attempt tool=list_transactions attempt=1 max_attempts=3" in messages[0]
    assert "tool_retry_attempt tool=list_transactions attempt=2 max_attempts=3" in messages[1]


def test_an_open_breaker_refuses_before_any_attempt() -> None:
    inner = _ScriptedPort(responses=[_PAGE])
    breaker = _FakeBreaker(allowed=False)
    port = _port(inner, breaker=breaker, max_attempts=3)

    result = port.list_transactions(_FILTERS)

    assert isinstance(result, ToolFailure)
    assert result.tool is Tool.LIST_TRANSACTIONS
    assert result.cause == "circuit_open"
    assert inner.calls == 0


@dataclass
class _AllMethodsPort:
    """Every ``ToolPort`` method returns its own distinct sentinel and records that it was
    called, so a single test can prove every one of ``RetriedToolPort``'s six methods delegates
    to the right underlying call."""

    calls: list[str] = field(default_factory=list)

    def list_transactions(self, filters: TransactionFilters) -> str:
        self.calls.append("list_transactions")
        return "list_transactions-result"

    def get_transaction(self, ref: str) -> str:
        self.calls.append("get_transaction")
        return "get_transaction-result"

    def list_dispute_cases(self) -> str:
        self.calls.append("list_dispute_cases")
        return "list_dispute_cases-result"

    def get_case(self, case_number: str) -> str:
        self.calls.append("get_case")
        return "get_case-result"

    def evaluate_dispute(self, request: object) -> str:
        self.calls.append("evaluate_dispute")
        return "evaluate_dispute-result"

    def create_dispute_case(self, request: object) -> str:
        self.calls.append("create_dispute_case")
        return "create_dispute_case-result"


def test_every_tool_port_method_delegates_to_its_own_underlying_call() -> None:
    """Duck-typed on purpose: this proves delegation, not the real contract types, which the
    other tests in this file already exercise against real domain values."""
    inner = _AllMethodsPort()
    policy = RetryPolicy(max_attempts=3, base_delay_ms=10, max_delay_ms=20)
    port: Any = RetriedToolPort(inner, policy=policy, breaker=_FakeBreaker(), sleep=_no_sleep)  # type: ignore[arg-type]

    assert port.list_transactions(_FILTERS) == "list_transactions-result"
    assert port.get_transaction("ref") == "get_transaction-result"
    assert port.list_dispute_cases() == "list_dispute_cases-result"
    assert port.get_case("case") == "get_case-result"
    assert port.evaluate_dispute(object()) == "evaluate_dispute-result"
    assert port.create_dispute_case(object()) == "create_dispute_case-result"
    assert inner.calls == [
        "list_transactions",
        "get_transaction",
        "list_dispute_cases",
        "get_case",
        "evaluate_dispute",
        "create_dispute_case",
    ]


def test_a_none_result_from_a_lookup_method_counts_as_success() -> None:
    """``get_transaction``/``get_case`` legitimately return ``None`` for no match — that is not a
    failure, and must still close the breaker like any other successful call."""

    @dataclass
    class _LookupPort:
        def get_transaction(self, ref: str) -> None:
            return None

    breaker = _FakeBreaker()
    policy = RetryPolicy(max_attempts=3, base_delay_ms=10, max_delay_ms=20)
    port = RetriedToolPort(_LookupPort(), policy=policy, breaker=breaker, sleep=_no_sleep)  # type: ignore[arg-type]

    result = port.get_transaction("tx-1")

    assert result is None
    assert breaker.successes == 1
    assert breaker.failures == 0
