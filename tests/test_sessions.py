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
    ISSUER,
    AgentPrincipal,
    InMemoryRevocationStore,
    Principal,
    SessionRejected,
    SessionService,
)

KEY = SecretStr("k" * 40)
AGENT_KEY = SecretStr("g" * 40)
AUDIENCE = "customer"
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
    return SessionService({"customer": KEY, "agent": AGENT_KEY}, 900, clock=clock)


def _claims(clock: Clock, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "C1",
        "sid": "session-1",
        "iat": int(clock.now.timestamp()),
        "exp": int((clock.now + timedelta(minutes=15)).timestamp()),
        "demo": False,
    }
    return {**base, **overrides}


def _forge(
    claims: dict[str, object],
    key: str = "k" * 40,
    algorithm: str = "HS256",
    kid: str | None = "customer",
) -> str:
    headers = {"kid": kid} if kid is not None else None
    return jwt.encode(claims, key, algorithm=algorithm, headers=headers)


def _code(service: SessionService, token: str, *, audience: str = "customer") -> ErrorCode:
    verify = service.verify_customer if audience == "customer" else service.verify_agent
    with pytest.raises(SessionRejected) as raised:
        verify(token)
    return raised.value.code


# -----------------------------------------------------------------------------
# Issuing and verifying
# -----------------------------------------------------------------------------


def test_a_token_identifies_the_customer_it_was_issued_for(service: SessionService) -> None:
    """Round trip: the principal carries the customer, the session and the lifetime."""
    issued = service.issue("CUST-0042", audience="customer")

    principal = service.verify_customer(issued.token)

    assert principal.customer_id == "CUST-0042"
    assert principal.session_id == issued.session_id
    assert principal.issued_at == START
    assert principal.expires_at == START + timedelta(seconds=900) == issued.expires_at


def test_every_session_gets_a_new_identifier(service: SessionService) -> None:
    """Session identifiers are unique and unguessable in length."""
    first, second = (
        service.issue("C1", audience="customer"),
        service.issue("C1", audience="customer"),
    )

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
        service.issue(customer_id, audience="customer")


def test_the_last_second_of_the_lifetime_is_valid_and_the_next_is_expired(
    service: SessionService, clock: Clock
) -> None:
    """Expiry is exclusive: at the expiry instant the session is over."""
    token = service.issue("C1", audience="customer").token

    clock.now = START + timedelta(seconds=899)
    assert service.verify_customer(token).customer_id == "C1"
    clock.now = START + timedelta(seconds=900)
    assert _code(service, token) is ErrorCode.SESSION_EXPIRED
    clock.now = START + timedelta(days=30)
    assert _code(service, token) is ErrorCode.SESSION_EXPIRED


def test_the_lifetime_is_the_configured_one(clock: Clock) -> None:
    """A shorter configured lifetime shortens the token."""
    short = SessionService({"customer": KEY}, 60, clock=clock)
    token = short.issue("C1", audience="customer").token

    clock.now = START + timedelta(seconds=60)

    assert _code(short, token) is ErrorCode.SESSION_EXPIRED


def test_an_explicit_ttl_overrides_the_configured_default(
    service: SessionService, clock: Clock
) -> None:
    """A demo customer session (30 minutes) can differ from the configured default."""
    issued = service.issue("C1", audience="customer", ttl=timedelta(minutes=30))

    assert issued.expires_at == START + timedelta(minutes=30)


def test_at_least_one_audience_is_required(clock: Clock) -> None:
    """A service with no signing key configured for any audience cannot issue or verify."""
    with pytest.raises(ValueError, match="at least one audience"):
        SessionService({}, 900, clock=clock)


def test_issuing_for_an_unconfigured_audience_is_refused(service: SessionService) -> None:
    """Only the audiences a signing key was actually provided for can be issued."""
    with pytest.raises(ValueError, match="no signing key configured"):
        service.issue("C1", audience="unknown")


def test_a_demo_flag_round_trips_and_an_agent_token_yields_an_agent_principal(
    service: SessionService,
) -> None:
    """``AgentPrincipal`` is a distinct type from ``Principal``, not a flag on
    one shared type — a route written against one type cannot silently accept the other."""
    issued = service.issue("A1", audience="agent", demo=True)

    principal = service.verify_agent(issued.token)

    assert isinstance(principal, AgentPrincipal)
    assert principal.agent_id == "A1"
    assert principal.demo is True


def test_a_customer_token_and_an_agent_token_use_different_keys(
    service: SessionService, clock: Clock
) -> None:
    """Distinct audiences never share a signing key, per audience."""
    customer_token = service.issue("C1", audience="customer").token
    agent_token = service.issue("A1", audience="agent").token

    # A customer key cannot verify a token minted under the agent key's kid, and vice versa —
    # proven directly by forging what a key confusion attack would produce.
    forged_as_agent = _forge(_claims(clock, aud="agent"), key="k" * 40, kid="agent")
    forged_as_customer = _forge(_claims(clock, aud="customer"), key="g" * 40, kid="customer")

    assert isinstance(service.verify_customer(customer_token), Principal)
    assert isinstance(service.verify_agent(agent_token), AgentPrincipal)
    assert _code(service, forged_as_agent, audience="agent") is ErrorCode.SESSION_INVALID
    assert _code(service, forged_as_customer, audience="customer") is ErrorCode.SESSION_INVALID


def test_a_token_of_the_right_signature_but_the_wrong_audience_method_is_refused(
    service: SessionService,
) -> None:
    """``verify_customer``/``verify_agent`` refuse a validly signed token of the other audience,
    exactly like a bad signature — the split itself is the audience check, not a field compared
    after the fact."""
    customer_token = service.issue("C1", audience="customer").token
    agent_token = service.issue("A1", audience="agent").token

    assert _code(service, customer_token, audience="agent") is ErrorCode.SESSION_INVALID
    assert _code(service, agent_token, audience="customer") is ErrorCode.SESSION_INVALID


@pytest.mark.parametrize("kid", [None, "unknown-audience"])
def test_a_missing_or_unknown_key_selector_is_invalid(
    service: SessionService, clock: Clock, kid: str | None
) -> None:
    """A token naming no audience, or one this service holds no key for, is refused."""
    assert _code(service, _forge(_claims(clock), kid=kid)) is ErrorCode.SESSION_INVALID


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
    issued = service.issue("C1", audience="customer")
    principal = service.verify_customer(issued.token)

    service.revoke(principal)

    assert _code(service, issued.token) is ErrorCode.SESSION_REVOKED
    clock.now = START + timedelta(seconds=900)
    assert _code(service, issued.token) is ErrorCode.SESSION_EXPIRED


def test_revoking_one_session_leaves_the_others(service: SessionService) -> None:
    """Sessions are independent, also for the same customer."""
    first, second = (
        service.issue("C1", audience="customer"),
        service.issue("C1", audience="customer"),
    )

    service.revoke(service.verify_customer(first.token))

    assert service.verify_customer(second.token).customer_id == "C1"


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
