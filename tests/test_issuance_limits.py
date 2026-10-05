"""
Issuance Limiter Tests
=======================

Component: ``app.security.issuance_limits``. Hermetic: the clock is injected and moved by hand.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

from app.security.issuance_limits import IssuanceLimiter, SessionReservations

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


TTL = timedelta(minutes=30)


def _reserve_three(limiter: IssuanceLimiter) -> list[tuple[str, datetime]]:
    """What one sign-in takes: an address, the whole broker and one persona slot."""
    reserved = []
    for key in ("address:a", "global:customer", "persona:ana"):
        expiry = limiter.try_reserve(key, cap=1, ttl=TTL)
        assert expiry is not None
        reserved.append((key, expiry))
    return reserved


def test_releasing_a_session_frees_every_reservation_it_held() -> None:
    clock = Clock()
    limiter = IssuanceLimiter(clock=clock)
    held = SessionReservations(limiter, clock=clock)
    held.hold("s1", _reserve_three(limiter))

    held.release("s1")

    for key in ("address:a", "global:customer", "persona:ana"):
        assert limiter.try_reserve(key, cap=1, ttl=TTL) is not None, f"{key} was not freed"


def test_a_session_that_is_never_released_keeps_its_reservations_until_their_ttl() -> None:
    clock = Clock()
    limiter = IssuanceLimiter(clock=clock)
    held = SessionReservations(limiter, clock=clock)
    held.hold("s1", _reserve_three(limiter))

    assert limiter.try_reserve("persona:ana", cap=1, ttl=TTL) is None
    clock.now = START + TTL
    assert limiter.try_reserve("persona:ana", cap=1, ttl=TTL) is not None


def test_releasing_twice_does_not_free_a_reservation_another_session_has_since_taken() -> None:
    clock = Clock()
    limiter = IssuanceLimiter(clock=clock)
    held = SessionReservations(limiter, clock=clock)
    held.hold("s1", _reserve_three(limiter))
    held.release("s1")
    held.hold("s2", _reserve_three(limiter))

    held.release("s1")

    assert limiter.try_reserve("persona:ana", cap=1, ttl=TTL) is None
    assert limiter.try_reserve("address:a", cap=1, ttl=TTL) is None
    assert limiter.try_reserve("global:customer", cap=1, ttl=TTL) is None


def test_releasing_a_session_that_expired_does_not_free_the_slot_taken_after_it() -> None:
    clock = Clock()
    limiter = IssuanceLimiter(clock=clock)
    held = SessionReservations(limiter, clock=clock)
    held.hold("s1", _reserve_three(limiter))
    clock.now = START + TTL + timedelta(minutes=1)
    held.hold("s2", _reserve_three(limiter))

    held.release("s1")

    assert limiter.try_reserve("persona:ana", cap=1, ttl=TTL) is None


def test_releasing_an_unknown_session_changes_nothing() -> None:
    clock = Clock()
    limiter = IssuanceLimiter(clock=clock)
    held = SessionReservations(limiter, clock=clock)
    held.hold("s1", _reserve_three(limiter))

    held.release("never-issued")

    assert limiter.try_reserve("persona:ana", cap=1, ttl=TTL) is None


def _hold_one(
    held: SessionReservations, limiter: IssuanceLimiter, session_id: str, ttl: timedelta
) -> None:
    key = f"k:{session_id}"
    expiry = limiter.try_reserve(key, cap=1, ttl=ttl)
    assert expiry is not None
    held.hold(session_id, [(key, expiry)])


def test_a_full_record_drops_an_expired_session_before_an_older_live_one() -> None:
    clock = Clock()
    limiter = IssuanceLimiter(clock=clock)
    held = SessionReservations(limiter, clock=clock, capacity=2)
    _hold_one(held, limiter, "live", TTL)
    _hold_one(held, limiter, "expired", timedelta(minutes=5))
    clock.now = START + timedelta(minutes=10)
    _hold_one(held, limiter, "new", TTL)

    held.release("live")
    held.release("new")

    assert limiter.try_reserve("k:live", 1, TTL) is not None, "the older live entry was dropped"
    assert limiter.try_reserve("k:new", 1, TTL) is not None, "the newest entry was dropped"


def test_a_full_record_with_nothing_expired_drops_the_oldest() -> None:
    clock = Clock()
    limiter = IssuanceLimiter(clock=clock)
    held = SessionReservations(limiter, clock=clock, capacity=2)
    _hold_one(held, limiter, "first", TTL)
    _hold_one(held, limiter, "second", TTL)
    _hold_one(held, limiter, "third", TTL)

    held.release("first")
    held.release("third")

    assert limiter.try_reserve("k:third", 1, TTL) is not None, "the newest entry was dropped"
    assert limiter.try_reserve("k:first", 1, TTL) is None, "the oldest live entry was kept"


def test_simultaneous_releases_of_one_session_free_its_reservation_once() -> None:
    """Two sessions share a persona key at the same expiry; releasing one from many threads must
    take exactly one of the two entries, never both."""
    clock = Clock()
    limiter = IssuanceLimiter(clock=clock)
    held = SessionReservations(limiter, clock=clock)
    for session_id in ("s1", "s2"):
        expiry = limiter.try_reserve("persona:ana", cap=2, ttl=TTL)
        assert expiry is not None
        held.hold(session_id, [("persona:ana", expiry)])
    original = limiter.release

    def slow_release(key: str, expiry: datetime) -> None:
        time.sleep(0.002)
        original(key, expiry)

    limiter.release = slow_release  # type: ignore[method-assign]
    barrier = threading.Barrier(16)

    def attempt() -> None:
        barrier.wait()
        held.release("s1")

    threads = [threading.Thread(target=attempt) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert limiter.try_reserve("persona:ana", cap=2, ttl=TTL) is not None
    assert limiter.try_reserve("persona:ana", cap=2, ttl=TTL) is None
