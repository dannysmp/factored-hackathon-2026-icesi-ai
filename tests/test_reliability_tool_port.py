"""
Bounded Retry for the Tool Port Tests
=======================================

Component: ``app.reliability.tool_port``. Hermetic: the sleep between attempts, the circuit
breaker and the wrapped ``ToolPort`` are all fakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

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
    """Returns exactly what was queued for ``list_transactions``, in order."""

    responses: list[TransactionPage | ToolFailure] = field(default_factory=list)
    calls: int = 0

    def list_transactions(self, filters: TransactionFilters) -> TransactionPage | ToolFailure:
        self.calls += 1
        return self.responses[self.calls - 1]

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


def _port(inner: _ScriptedPort, *, breaker: _FakeBreaker, max_attempts: int) -> RetriedToolPort:
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


def test_a_non_retryable_failure_is_returned_immediately_and_never_touches_the_breaker() -> None:
    """A permission or not-found refusal is a correctness outcome, not infrastructure noise."""
    failure = ToolFailure(tool=Tool.LIST_TRANSACTIONS, cause="error", retryable=False)
    inner = _ScriptedPort(responses=[failure])
    breaker = _FakeBreaker()
    port = _port(inner, breaker=breaker, max_attempts=3)

    result = port.list_transactions(_FILTERS)

    assert result is failure
    assert inner.calls == 1
    assert breaker.successes == 0
    assert breaker.failures == 0
    assert _SLEEPS == []


def test_a_circuit_open_failure_from_the_inner_port_is_never_retried_either() -> None:
    """Retrying the breaker's own signal would defeat it — even if the inner port somehow
    returned one directly, it is passed straight through, not retried."""
    failure = ToolFailure(tool=Tool.LIST_TRANSACTIONS, cause="circuit_open")
    inner = _ScriptedPort(responses=[failure])
    breaker = _FakeBreaker()
    port = _port(inner, breaker=breaker, max_attempts=3)

    result = port.list_transactions(_FILTERS)

    assert result is failure
    assert inner.calls == 1
    assert _SLEEPS == []


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
