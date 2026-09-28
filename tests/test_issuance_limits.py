"""
Issuance Limiter Tests
=======================

Component: ``app.security.issuance_limits``. Hermetic: the clock is injected and moved by hand.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

from app.security.issuance_limits import IssuanceLimiter

START = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)


class Clock:
    """A clock that only moves when the test says so."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def test_reservations_are_allowed_up_to_the_cap_and_refused_beyond_it() -> None:
    limiter = IssuanceLimiter(clock=Clock())

    first = limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=30))
    second = limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=30))

    assert first is not None
    assert second is not None
    assert limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=30)) is None


def test_a_reservation_frees_itself_once_its_own_ttl_elapses() -> None:
    clock = Clock()
    limiter = IssuanceLimiter(clock=clock)
    limiter.try_reserve("addr", cap=1, ttl=timedelta(minutes=30))

    assert limiter.try_reserve("addr", cap=1, ttl=timedelta(minutes=30)) is None
    clock.now = START + timedelta(minutes=30)
    assert limiter.try_reserve("addr", cap=1, ttl=timedelta(minutes=30)) is not None


def test_reservations_with_different_ttls_expire_independently() -> None:
    """A short-lived reservation frees its slot while a longer one, made earlier, still holds."""
    clock = Clock()
    limiter = IssuanceLimiter(clock=clock)
    limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=60))  # agent-length
    limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=30))  # customer-length

    clock.now = START + timedelta(minutes=31)

    # The 30-minute reservation is gone; the 60-minute one still holds one slot.
    assert limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=30)) is not None
    assert limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=30)) is None


def test_keys_are_counted_separately() -> None:
    limiter = IssuanceLimiter(clock=Clock())
    limiter.try_reserve("persona:ana", cap=1, ttl=timedelta(minutes=30))

    assert limiter.try_reserve("persona:ana", cap=1, ttl=timedelta(minutes=30)) is None
    assert limiter.try_reserve("persona:joao", cap=1, ttl=timedelta(minutes=30)) is not None


def test_the_table_is_bounded() -> None:
    """A flood of distinct keys cannot grow memory without limit; the oldest is dropped."""
    limiter = IssuanceLimiter(clock=Clock(), capacity=3)

    for name in ("a", "b", "c", "d"):
        limiter.try_reserve(name, cap=1, ttl=timedelta(minutes=30))

    # The oldest ("a") was dropped, so its key looks free again; "d" is still within its own cap.
    assert limiter.try_reserve("a", cap=1, ttl=timedelta(minutes=30)) is not None
    assert limiter.try_reserve("d", cap=1, ttl=timedelta(minutes=30)) is None


def test_releasing_a_reservation_frees_its_slot_immediately() -> None:
    """A caller checking several keys for one attempt can undo an earlier success on a later
    refusal, so a blocked attempt never burns capacity meant for a completed one."""
    limiter = IssuanceLimiter(clock=Clock())
    expiry = limiter.try_reserve("addr", cap=1, ttl=timedelta(minutes=30))
    assert expiry is not None
    assert limiter.try_reserve("addr", cap=1, ttl=timedelta(minutes=30)) is None

    limiter.release("addr", expiry)

    assert limiter.try_reserve("addr", cap=1, ttl=timedelta(minutes=30)) is not None


def test_releasing_an_unknown_reservation_is_a_no_op() -> None:
    limiter = IssuanceLimiter(clock=Clock())

    limiter.release("never-reserved", START)  # no key at all

    assert limiter.try_reserve("never-reserved", cap=1, ttl=timedelta(minutes=30)) is not None


def test_releasing_one_of_two_reservations_for_a_key_keeps_the_other() -> None:
    limiter = IssuanceLimiter(clock=Clock())
    first = limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=30))
    second = limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=30))
    assert first is not None
    assert second is not None

    limiter.release("addr", first)

    # One slot freed, one still held: a new reservation succeeds, a further one does not.
    assert limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=30)) is not None
    assert limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=30)) is None


def test_releasing_an_expiry_not_present_for_a_key_that_still_holds_others_is_a_no_op() -> None:
    """The key exists (another reservation is still live), but this exact expiry is not one of
    its entries — releasing it changes nothing, rather than removing the wrong entry."""
    limiter = IssuanceLimiter(clock=Clock())
    kept = limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=30))
    assert kept is not None

    limiter.release("addr", kept + timedelta(seconds=1))  # not a real reservation's expiry

    # The genuine reservation is untouched: a second slot is still available, a third is not.
    assert limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=30)) is not None
    assert limiter.try_reserve("addr", cap=2, ttl=timedelta(minutes=30)) is None


def test_concurrent_reservations_cannot_all_pass_the_check() -> None:
    """Forty simultaneous reservations for one key: at most the cap get through."""
    limiter = IssuanceLimiter(clock=Clock())
    original = limiter._live

    def slow_live(key: str, now: datetime) -> list[datetime]:
        found = original(key, now)
        time.sleep(0.002)
        return found

    limiter._live = slow_live  # type: ignore[method-assign]
    barrier = threading.Barrier(40)
    results: list[datetime | None] = []

    def attempt() -> None:
        barrier.wait()
        results.append(limiter.try_reserve("key", cap=5, ttl=timedelta(minutes=30)))

    threads = [threading.Thread(target=attempt) for _ in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(1 for result in results if result is not None) == 5
    assert len(results) == 40
