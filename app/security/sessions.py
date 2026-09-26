"""
Session Tokens
==============

Overview
--------
Issues and verifies the short-lived session tokens that bind a request to one customer. A token
is a signed JWT carrying the customer identifier and a session identifier; the service trusts
nothing about a customer except what a valid, unexpired, unrevoked token says.

Scope
-----
In: issuing, verifying and revoking sessions; the revocation store interface and its in-memory
implementation.
Out: how a customer proves who they are (the sandbox login route), HTTP handling, and the tools
that read customer data.

Design Principles
-----------------
- One algorithm (HS256) is accepted, ``none`` and every other are refused, and issuer and audience
  are checked, so a token from another system cannot be replayed here.
- The clock is injected. Expiry is checked against it, after the signature is verified, so only a
  validly signed token can be reported as expired.
- Tokens are short-lived and revocable; revocation entries live only as long as the token would.
- A customer identifier is opaque and validated against a strict pattern before it is signed or
  trusted.

Runtime Contract
----------------
``SessionService.issue(customer_id) -> IssuedSession``
``SessionService.verify(token) -> Principal`` raises ``SessionRejected`` with an ``ErrorCode``.
``SessionService.revoke(principal)``

Limitations
-----------
The revocation store is in memory: revocations are lost on restart and are not shared between
processes. Tokens still expire on their own; a shared store (the operational database) replaces
it when the service runs as more than one process.
"""

from __future__ import annotations

# Standard libraries
import re  # Strict customer-identifier pattern
import secrets  # Random session identifiers
import threading  # The in-memory store is shared between request threads
from collections.abc import Callable  # Type of the injected clock
from dataclasses import dataclass  # Immutable principal and issued-session objects
from datetime import UTC, datetime, timedelta  # Token timestamps
from typing import Protocol  # Interface of the revocation store

# Third-party libraries
import jwt  # Signing and verifying the tokens
from pydantic import SecretStr  # Signing key that never prints

# Local modules
from app.security.errors import ErrorCode  # Reasons a session is refused

Clock = Callable[[], datetime]

ISSUER = "dispute-intake"
AUDIENCE = "dispute-intake-api"
ALGORITHM = "HS256"
CUSTOMER_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,20}")
MAX_REVOCATIONS = 100_000


def utc_now() -> datetime:
    """The current time in UTC; the production clock."""
    return datetime.now(UTC)


class SessionRejected(Exception):
    """A session token was refused; ``code`` says why."""

    def __init__(self, code: ErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated customer behind a request."""

    customer_id: str
    session_id: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A newly issued session: the token to give the client and when it stops working."""

    token: str
    session_id: str
    expires_at: datetime


class RevocationStore(Protocol):
    """Remembers which sessions were revoked until they would have expired anyway."""

    def revoke(self, session_id: str, expires_at: datetime, now: datetime) -> None:
        """Record ``session_id`` as revoked until ``expires_at``."""

    def is_revoked(self, session_id: str, now: datetime) -> bool:
        """Whether ``session_id`` was revoked and has not yet expired."""


class InMemoryRevocationStore:
    """Thread-safe, bounded, in-process revocation store.

    When the bound is reached after purging expired entries, revoking fails loudly instead of
    forgetting a revocation.
    """

    def __init__(self, capacity: int = MAX_REVOCATIONS) -> None:
        self._capacity = capacity
        self._entries: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def revoke(self, session_id: str, expires_at: datetime, now: datetime) -> None:
        """Record the revocation; purge expired entries first."""
        with self._lock:
            self._entries = {k: v for k, v in self._entries.items() if v > now}
            if session_id not in self._entries and len(self._entries) >= self._capacity:
                raise RuntimeError("revocation store is full")
            self._entries[session_id] = expires_at

    def is_revoked(self, session_id: str, now: datetime) -> bool:
        """Whether the session is revoked and its token has not yet expired."""
        with self._lock:
            expires_at = self._entries.get(session_id)
        return expires_at is not None and expires_at > now


class SessionService:
    """Issues, verifies and revokes session tokens."""

    def __init__(
        self,
        signing_key: SecretStr,
        ttl_seconds: int,
        *,
        clock: Clock = utc_now,
        revocations: RevocationStore | None = None,
    ) -> None:
        self._key = signing_key
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._revocations: RevocationStore = revocations or InMemoryRevocationStore()

    def now(self) -> datetime:
        """The current time according to the injected clock."""
        return self._clock()

    def issue(self, customer_id: str) -> IssuedSession:
        """Issue a token for ``customer_id`` valid for the configured lifetime.

        Raises
        ------
        ValueError
            When ``customer_id`` does not match the identifier pattern.
        """
        if not CUSTOMER_ID_PATTERN.fullmatch(customer_id):
            raise ValueError("customer identifier has an invalid format")
        issued_at = self._clock()
        expires_at = issued_at + self._ttl
        session_id = secrets.token_urlsafe(16)
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": customer_id,
            "sid": session_id,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        token = jwt.encode(claims, self._key.get_secret_value(), algorithm=ALGORITHM)
        return IssuedSession(token=token, session_id=session_id, expires_at=expires_at)

    def verify(self, token: str) -> Principal:
        """Return the principal a valid token identifies.

        Raises
        ------
        SessionRejected
            ``SESSION_INVALID`` for anything wrong with the token itself, ``SESSION_EXPIRED``
            for a validly signed token past its expiry, ``SESSION_REVOKED`` after logout.
        """
        # Verify the signature, algorithm, issuer, audience and the presence of every claim
        try:
            claims = jwt.decode(
                token,
                self._key.get_secret_value(),
                algorithms=[ALGORITHM],
                audience=AUDIENCE,
                issuer=ISSUER,
                options={
                    "verify_exp": False,
                    "verify_iat": False,
                    "require": ["exp", "iat", "sub", "sid", "iss", "aud"],
                },
            )
        except jwt.InvalidTokenError:
            raise SessionRejected(ErrorCode.SESSION_INVALID) from None

        # Check the claims' types and the identifier pattern before trusting any of them
        customer_id, session_id = claims["sub"], claims["sid"]
        issued, expires = claims["iat"], claims["exp"]
        valid = (
            isinstance(customer_id, str)
            and CUSTOMER_ID_PATTERN.fullmatch(customer_id)
            and isinstance(session_id, str)
            and bool(session_id)
            and all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in (issued, expires)
            )
        )
        if not valid:
            raise SessionRejected(ErrorCode.SESSION_INVALID)

        # Expiry and issue time are judged against the injected clock, after the signature
        now = self._clock()
        expires_at = datetime.fromtimestamp(expires, UTC)
        issued_at = datetime.fromtimestamp(issued, UTC)
        if issued_at > now:
            raise SessionRejected(ErrorCode.SESSION_INVALID)
        if expires_at <= now:
            raise SessionRejected(ErrorCode.SESSION_EXPIRED)
        if self._revocations.is_revoked(session_id, now):
            raise SessionRejected(ErrorCode.SESSION_REVOKED)
        return Principal(customer_id, session_id, issued_at, expires_at)

    def revoke(self, principal: Principal) -> None:
        """Revoke the session of ``principal`` until it would have expired."""
        self._revocations.revoke(principal.session_id, principal.expires_at, self._clock())
