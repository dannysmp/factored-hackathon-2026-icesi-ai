"""
Circuit Breaker Tests
======================

Component: ``app.reliability.breaker``. Hermetic: the clock is injected and moved by hand.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.reliability.breaker import InMemoryCircuitBreaker

_START = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


class _Clock:
    """A clock that only moves when the test says so."""

    def __init__(self) -> None:
        self.now = _START

    def __call__(self) -> datetime:
        return self.now


def test_a_fresh_breaker_allows_every_call() -> None:
    breaker = InMemoryCircuitBreaker(3, 30, clock=_Clock())

    assert breaker.allow()
    assert breaker.allow()


def test_success_never_moves_a_closed_breaker() -> None:
    breaker = InMemoryCircuitBreaker(3, 30, clock=_Clock())

    breaker.record_success()

    assert breaker.allow()


def test_fewer_failures_than_the_threshold_stay_closed() -> None:
    breaker = InMemoryCircuitBreaker(3, 30, clock=_Clock())

    breaker.record_failure()
    breaker.record_failure()

    assert breaker.allow()


def test_reaching_the_failure_threshold_opens_the_breaker() -> None:
    breaker = InMemoryCircuitBreaker(3, 30, clock=_Clock())

    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()

    assert not breaker.allow()


def test_a_success_before_the_threshold_resets_the_failure_count() -> None:
    """Two failures, then a success, then two more failures: still closed — the count did not
    carry across the intervening success."""
    breaker = InMemoryCircuitBreaker(3, 30, clock=_Clock())

    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()

    assert breaker.allow()


def test_an_open_breaker_refuses_until_the_reset_window_elapses() -> None:
    clock = _Clock()
    breaker = InMemoryCircuitBreaker(1, 30, clock=clock)
    breaker.record_failure()
    assert not breaker.allow()

    clock.now = _START + timedelta(seconds=29)
    assert not breaker.allow()


def test_an_open_breaker_allows_exactly_one_trial_after_the_reset_window() -> None:
    clock = _Clock()
    breaker = InMemoryCircuitBreaker(1, 30, clock=clock)
    breaker.record_failure()
    clock.now = _START + timedelta(seconds=30)

    assert breaker.allow()
    # A second caller, before the trial's own outcome is recorded, is refused.
    assert not breaker.allow()


def test_a_successful_trial_closes_the_breaker() -> None:
    clock = _Clock()
    breaker = InMemoryCircuitBreaker(1, 30, clock=clock)
    breaker.record_failure()
    clock.now = _START + timedelta(seconds=30)
    assert breaker.allow()

    breaker.record_success()

    assert breaker.allow()


def test_closing_resets_the_failure_count_not_just_the_open_state() -> None:
    """A breaker that closed after a trial forgets the failures that opened it — a threshold of
    two needs two FRESH failures to reopen, not one, proving the count itself was reset."""
    clock = _Clock()
    breaker = InMemoryCircuitBreaker(2, 30, clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    clock.now = _START + timedelta(seconds=30)
    assert breaker.allow()
    breaker.record_success()

    breaker.record_failure()

    assert breaker.allow()


def test_a_failed_trial_reopens_the_breaker_for_a_fresh_window() -> None:
    clock = _Clock()
    breaker = InMemoryCircuitBreaker(1, 30, clock=clock)
    breaker.record_failure()
    clock.now = _START + timedelta(seconds=30)
    assert breaker.allow()

    breaker.record_failure()

    assert not breaker.allow()
    clock.now = _START + timedelta(seconds=59)
    assert not breaker.allow()
    clock.now = _START + timedelta(seconds=60)
    assert breaker.allow()


def test_a_healthy_closed_breaker_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    breaker = InMemoryCircuitBreaker(3, 30, clock=_Clock(), name="llm")

    with caplog.at_level(logging.INFO, logger="app.reliability.breaker"):
        breaker.record_success()
        breaker.record_failure()
        assert breaker.allow()

    assert caplog.records == []


def test_opening_logs_a_warning_naming_the_dependency_and_the_failure_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    breaker = InMemoryCircuitBreaker(2, 30, clock=_Clock(), name="llm")

    with caplog.at_level(logging.INFO, logger="app.reliability.breaker"):
        breaker.record_failure()
        assert caplog.records == []  # below threshold: no transition yet
        breaker.record_failure()

    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "WARNING"
    assert "circuit_breaker_opened" in caplog.records[0].message
    assert "dependency=llm" in caplog.records[0].message
    assert "failures=2" in caplog.records[0].message


def test_a_failed_trial_logs_a_reopened_warning(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    breaker = InMemoryCircuitBreaker(1, 30, clock=clock, name="tool")
    breaker.record_failure()
    clock.now = _START + timedelta(seconds=30)
    assert breaker.allow()
    caplog.clear()  # drop the opening warning logged above; only the reopen matters here

    with caplog.at_level(logging.INFO, logger="app.reliability.breaker"):
        breaker.record_failure()

    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "WARNING"
    assert "circuit_breaker_reopened" in caplog.records[0].message
    assert "dependency=tool" in caplog.records[0].message


def test_a_successful_trial_logs_an_info_recovery(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    breaker = InMemoryCircuitBreaker(1, 30, clock=clock, name="tool")
    breaker.record_failure()
    clock.now = _START + timedelta(seconds=30)
    assert breaker.allow()
    caplog.clear()  # drop the opening warning logged above; only the recovery matters here

    with caplog.at_level(logging.INFO, logger="app.reliability.breaker"):
        breaker.record_success()

    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "INFO"
    assert "circuit_breaker_closed" in caplog.records[0].message
    assert "dependency=tool" in caplog.records[0].message


def test_a_success_on_an_already_closed_breaker_logs_no_redundant_recovery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    breaker = InMemoryCircuitBreaker(3, 30, clock=_Clock(), name="llm")

    with caplog.at_level(logging.INFO, logger="app.reliability.breaker"):
        breaker.record_success()

    assert caplog.records == []
