"""
Session Token Tests
===================

Component: ``app.security.sessions`` and ``app.security.limits``. Hermetic: the clock is injected
and moved by hand, and tokens are forged with the same library to prove what is refused.
"""

from __future__ import annotations

# Standard libraries
import threading  # Concurrent attempts against the limiter
import time  # Widen the race window
from datetime import UTC, datetime, timedelta  # Controlled time

# Third-party libraries
import jwt  # Forge tokens to test refusal
import pytest  # Test runner and parametrisation
from pydantic import SecretStr  # Signing key

# Local modules
from app.security.errors import ErrorCode
from app.security.limits import AttemptLimiter
from app.security.sessions import (
    AUDIENCE,
    ISSUER,
    InMemoryRevocationStore,
    SessionRejected,
    SessionService,
)

KEY = SecretStr("k" * 40)
START = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)


class Clock:
    """A clock that only moves when the test says so."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(clock: Clock) -> SessionService:
    return SessionService(KEY, 900, clock=clock)


def _claims(clock: Clock, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "C1",
        "sid": "session-1",
        "iat": int(clock.now.timestamp()),
        "exp": int((clock.now + timedelta(minutes=15)).timestamp()),
    }
    return {**base, **overrides}


def _forge(claims: dict[str, object], key: str = "k" * 40, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, key, algorithm=algorithm)


def _code(service: SessionService, token: str) -> ErrorCode:
    with pytest.raises(SessionRejected) as raised:
        service.verify(token)
    return raised.value.code


# -----------------------------------------------------------------------------
# Issuing and verifying
# -----------------------------------------------------------------------------


def test_a_token_identifies_the_customer_it_was_issued_for(service: SessionService) -> None:
    """Round trip: the principal carries the customer, the session and the lifetime."""
    issued = service.issue("CUST-0042")

    principal = service.verify(issued.token)

    assert principal.customer_id == "CUST-0042"
    assert principal.session_id == issued.session_id
    assert principal.issued_at == START
    assert principal.expires_at == START + timedelta(seconds=900) == issued.expires_at


def test_every_session_gets_a_new_identifier(service: SessionService) -> None:
    """Session identifiers are unique and unguessable in length."""
    first, second = service.issue("C1"), service.issue("C1")

    assert first.session_id != second.session_id
    assert len(first.session_id) >= 16


@pytest.mark.parametrize(
    "customer_id", ["", "a" * 21, "has space", "../etc", "C1;drop", "ñandú", "C\n1", "C1\x00"]
)
def test_a_customer_identifier_with_an_unsafe_shape_is_never_signed(
    service: SessionService, customer_id: str
) -> None:
    """The identifier pattern is enforced before signing."""
    with pytest.raises(ValueError, match="invalid format"):
        service.issue(customer_id)


def test_the_last_second_of_the_lifetime_is_valid_and_the_next_is_expired(
    service: SessionService, clock: Clock
) -> None:
    """Expiry is exclusive: at the expiry instant the session is over."""
    token = service.issue("C1").token

    clock.now = START + timedelta(seconds=899)
    assert service.verify(token).customer_id == "C1"
    clock.now = START + timedelta(seconds=900)
    assert _code(service, token) is ErrorCode.SESSION_EXPIRED
    clock.now = START + timedelta(days=30)
    assert _code(service, token) is ErrorCode.SESSION_EXPIRED


def test_the_lifetime_is_the_configured_one(clock: Clock) -> None:
    """A shorter configured lifetime shortens the token."""
    short = SessionService(KEY, 60, clock=clock)
    token = short.issue("C1").token

    clock.now = START + timedelta(seconds=60)

    assert _code(short, token) is ErrorCode.SESSION_EXPIRED


# -----------------------------------------------------------------------------
# What is refused
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer x", "é" * 10, "." * 5])
def test_garbage_is_invalid_not_expired(service: SessionService, garbage: str) -> None:
    """Only a validly signed token can be reported as expired."""
    assert _code(service, garbage) is ErrorCode.SESSION_INVALID


def test_a_token_signed_with_another_key_is_invalid(service: SessionService, clock: Clock) -> None:
    """A forged signature is refused, even when the claims are perfect."""
    assert _code(service, _forge(_claims(clock), key="x" * 40)) is ErrorCode.SESSION_INVALID


def test_an_expired_token_with_a_bad_signature_is_invalid_not_expired(
    service: SessionService, clock: Clock
) -> None:
    """An attacker cannot learn anything from the expired code without the key."""
    old = _claims(clock, exp=int(clock.now.timestamp()) - 10)

    assert _code(service, _forge(old, key="x" * 40)) is ErrorCode.SESSION_INVALID
    assert _code(service, _forge(old)) is ErrorCode.SESSION_EXPIRED


def test_the_none_algorithm_is_refused(service: SessionService, clock: Clock) -> None:
    """An unsigned token is never accepted."""
    token = jwt.encode(_claims(clock), key="", algorithm="none")

    assert _code(service, token) is ErrorCode.SESSION_INVALID


def test_another_signing_algorithm_is_refused(service: SessionService, clock: Clock) -> None:
    """Only HS256 is accepted."""
    token = _forge(_claims(clock), key="k" * 64, algorithm="HS512")

    assert _code(service, token) is ErrorCode.SESSION_INVALID


@pytest.mark.parametrize("claim", ["iss", "aud"])
def test_a_token_for_another_issuer_or_audience_is_invalid(
    service: SessionService, clock: Clock, claim: str
) -> None:
    """A token minted for another system cannot be replayed here."""
    assert _code(service, _forge(_claims(clock, **{claim: "someone-else"}))) is (
        ErrorCode.SESSION_INVALID
    )


@pytest.mark.parametrize("missing", ["iss", "aud", "sub", "sid", "iat", "exp"])
def test_a_token_missing_any_claim_is_invalid(
    service: SessionService, clock: Clock, missing: str
) -> None:
    """Every claim is required."""
    claims = _claims(clock)
    del claims[missing]

    assert _code(service, _forge(claims)) is ErrorCode.SESSION_INVALID


@pytest.mark.parametrize(
    "overrides",
    [
        {"sub": 42},
        {"sub": "has space"},
        {"sub": "a" * 21},
        {"sub": ""},
        {"sid": ""},
        {"sid": 7},
        {"iat": "now"},
        {"exp": True},
        {"iat": True},
    ],
    ids=[
        "int-sub",
        "space",
        "long-sub",
        "empty-sub",
        "empty-sid",
        "int-sid",
        "text-iat",
        "bool-exp",
        "bool-iat",
    ],
)
def test_claims_of_the_wrong_type_or_shape_are_invalid(
    service: SessionService, clock: Clock, overrides: dict[str, object]
) -> None:
    """The types and shapes are checked before any claim is trusted."""
    assert _code(service, _forge(_claims(clock, **overrides))) is ErrorCode.SESSION_INVALID


def test_a_token_issued_in_the_future_is_invalid(service: SessionService, clock: Clock) -> None:
    """A token cannot be valid before it was issued."""
    future = _claims(clock, iat=int(clock.now.timestamp()) + 60)

    assert _code(service, _forge(future)) is ErrorCode.SESSION_INVALID


# -----------------------------------------------------------------------------
# Revocation
# -----------------------------------------------------------------------------


def test_a_revoked_session_is_refused_until_it_would_have_expired(
    service: SessionService, clock: Clock
) -> None:
    """Logout takes effect immediately and never outlives the token."""
    issued = service.issue("C1")
    principal = service.verify(issued.token)

    service.revoke(principal)

    assert _code(service, issued.token) is ErrorCode.SESSION_REVOKED
    clock.now = START + timedelta(seconds=900)
    assert _code(service, issued.token) is ErrorCode.SESSION_EXPIRED


def test_revoking_one_session_leaves_the_others(service: SessionService) -> None:
    """Sessions are independent, also for the same customer."""
    first, second = service.issue("C1"), service.issue("C1")

    service.revoke(service.verify(first.token))

    assert service.verify(second.token).customer_id == "C1"


def test_the_revocation_store_forgets_expired_entries_and_never_forgets_live_ones() -> None:
    """Bounded memory without un-revoking a live session."""
    store = InMemoryRevocationStore(capacity=2)
    store.revoke("a", START + timedelta(minutes=1), START)
    store.revoke("b", START + timedelta(minutes=10), START)

    with pytest.raises(RuntimeError, match="full"):
        store.revoke("c", START + timedelta(minutes=10), START)
    later = START + timedelta(minutes=2)
    store.revoke("c", START + timedelta(minutes=10), later)

    assert not store.is_revoked("a", later)
    assert store.is_revoked("b", later) and store.is_revoked("c", later)
    store.revoke("b", START + timedelta(minutes=10), later)  # re-revoking is allowed when full


# -----------------------------------------------------------------------------
# Attempt limiter
# -----------------------------------------------------------------------------


def test_attempts_beyond_the_limit_are_refused_until_the_window_passes() -> None:
    """Five attempts in a minute are allowed; the sixth waits until the first leaves the window."""
    clock = Clock()
    limiter = AttemptLimiter(max_failures=5, window_seconds=60, clock=clock)

    assert [limiter.begin_attempt("client") for _ in range(5)] == [0, 0, 0, 0, 0]
    assert limiter.begin_attempt("client") == 60

    clock.now = START + timedelta(seconds=59)
    assert limiter.begin_attempt("client") == 1
    clock.now = START + timedelta(seconds=60)
    assert limiter.begin_attempt("client") == 0


def test_a_refused_attempt_is_not_counted_again() -> None:
    """Waiting does not extend the wait: the count stops at the limit."""
    clock = Clock()
    limiter = AttemptLimiter(max_failures=2, window_seconds=60, clock=clock)
    limiter.begin_attempt("a")
    limiter.begin_attempt("a")

    for _ in range(10):
        assert limiter.begin_attempt("a") == 60

    clock.now = START + timedelta(seconds=60)
    assert limiter.begin_attempt("a") == 0


def test_clients_are_counted_separately() -> None:
    """One client's attempts never block another."""
    limiter = AttemptLimiter(max_failures=2, window_seconds=60, clock=Clock())
    limiter.begin_attempt("a")
    limiter.begin_attempt("a")

    assert limiter.begin_attempt("a") > 0
    assert limiter.begin_attempt("b") == 0


def test_the_limiter_table_is_bounded() -> None:
    """A flood of distinct clients cannot grow memory without limit; the oldest is dropped."""
    limiter = AttemptLimiter(max_failures=1, window_seconds=60, clock=Clock(), capacity=3)

    for name in ("a", "b", "c", "d"):
        limiter.begin_attempt(name)

    assert limiter.begin_attempt("a") == 0  # the oldest entry was dropped
    assert limiter.begin_attempt("d") > 0


def test_concurrent_attempts_cannot_all_pass_the_check() -> None:
    """Forty simultaneous attempts from one client: at most the limit get through."""
    limiter = AttemptLimiter(max_failures=5, window_seconds=60, clock=Clock())
    original = limiter._recent

    def slow_recent(key: str, now: datetime) -> list[datetime]:
        """Widen the window between checking and counting so an unlocked check would race."""
        found = original(key, now)
        time.sleep(0.002)
        return found

    limiter._recent = slow_recent  # type: ignore[method-assign]
    barrier = threading.Barrier(40)
    results: list[int] = []

    def attempt() -> None:
        barrier.wait()
        results.append(limiter.begin_attempt("client"))

    threads = [threading.Thread(target=attempt) for _ in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(0) == 5
    assert len(results) == 40
