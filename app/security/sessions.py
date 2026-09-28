"""
Session Tokens
==============

Overview
--------
Issues and verifies the short-lived session tokens that bind a request to one customer or agent.
A token is a signed JWT carrying the subject, a session identifier and which audience it belongs
to (ADR-18); the service trusts nothing about a subject except what a valid, unexpired, unrevoked
token of the right audience says.

Scope
-----
In: issuing, verifying and revoking sessions across audiences; the revocation store interface and
its in-memory implementation.
Out: how a customer or agent proves who they are (the sandbox login and the demo sign-in broker
routes), HTTP handling, and the tools that read customer data.

Design Principles
-----------------
- One algorithm (HS256) is accepted, ``none`` and every other are refused, and issuer and audience
  are checked, so a token from another system cannot be replayed here.
- **One signing key per audience, never one key shared across audiences** (ADR-18): a customer
  token and an agent token can never be confused for each other, even if one key were somehow
  compromised. The audience a token claims is carried in an unprotected JWT header (``kid``) used
  only to select which key to attempt verification with; the actual guarantee comes from that key
  successfully verifying the signature and the payload's own ``aud`` claim matching. A caller who
  does not hold a given audience's key cannot forge membership in it by lying in the header — the
  signature simply fails to verify.
- The clock is injected. Expiry is checked against it, after the signature is verified, so only a
  validly signed token can be reported as expired.
- Tokens are short-lived and revocable; revocation entries live only as long as the token would.
- A subject identifier is opaque and validated against a strict pattern before it is signed or
  trusted.

Runtime Contract
----------------
``SessionService.issue(subject, audience, ttl=None, *, demo=False) -> IssuedSession``
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
from collections.abc import Callable, Mapping  # Type of the injected clock and the key mapping
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
    """The authenticated subject behind a request, and which audience its token belongs to."""

    customer_id: str
    session_id: str
    issued_at: datetime
    expires_at: datetime
    audience: str
    demo: bool = False


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
    """Issues, verifies and revokes session tokens across audiences."""

    def __init__(
        self,
        signing_keys: Mapping[str, SecretStr],
        ttl_seconds: int,
        *,
        clock: Clock = utc_now,
        revocations: RevocationStore | None = None,
    ) -> None:
        if not signing_keys:
            raise ValueError("at least one audience's signing key is required")
        self._keys = dict(signing_keys)
        self._default_ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._revocations: RevocationStore = revocations or InMemoryRevocationStore()

    def now(self) -> datetime:
        """The current time according to the injected clock."""
        return self._clock()

    def issue(
        self,
        subject: str,
        audience: str,
        ttl: timedelta | None = None,
        *,
        demo: bool = False,
    ) -> IssuedSession:
        """Issue a token for ``subject`` in ``audience``, valid for ``ttl`` (or the default).

        Raises
        ------
        ValueError
            When ``subject`` does not match the identifier pattern, or ``audience`` has no
            signing key configured.
        """
        if not CUSTOMER_ID_PATTERN.fullmatch(subject):
            raise ValueError("subject identifier has an invalid format")
        if audience not in self._keys:
            raise ValueError(f"no signing key configured for audience {audience!r}")
        issued_at = self._clock()
        expires_at = issued_at + (ttl if ttl is not None else self._default_ttl)
        session_id = secrets.token_urlsafe(16)
        claims = {
            "iss": ISSUER,
            "aud": audience,
            "sub": subject,
            "sid": session_id,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "demo": demo,
        }
        # The audience also names the key: carried in the unprotected header only to select which
        # key to attempt verification with, never trusted on its own (see the module's Design
        # Principles). A forged header naming a key the signer does not hold cannot produce a
        # signature that key will verify.
        token = jwt.encode(
            claims,
            self._keys[audience].get_secret_value(),
            algorithm=ALGORITHM,
            headers={"kid": audience},
        )
        return IssuedSession(token=token, session_id=session_id, expires_at=expires_at)

    def verify(self, token: str) -> Principal:
        """Return the principal a valid token identifies.

        Raises
        ------
        SessionRejected
            ``SESSION_INVALID`` for anything wrong with the token itself, ``SESSION_EXPIRED``
            for a validly signed token past its expiry, ``SESSION_REVOKED`` after logout.
        """
        # The header's key selector is untrusted: it only picks which key to try. If it names an
        # audience this service holds no key for, or the token is malformed, that is refused
        # exactly like a bad signature would be — never a hint about what audiences exist.
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            raise SessionRejected(ErrorCode.SESSION_INVALID) from None
        kid = header.get("kid")
        if not isinstance(kid, str) or kid not in self._keys:
            raise SessionRejected(ErrorCode.SESSION_INVALID)

        # Verify the signature with that key, and the algorithm, issuer, audience and the
        # presence of every claim; the audience check below confirms the payload's own claim
        # matches the key that was actually used, so a mismatched "kid" simply fails to verify.
        try:
            claims = jwt.decode(
                token,
                self._keys[kid].get_secret_value(),
                algorithms=[ALGORITHM],
                audience=kid,
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
        demo = claims.get("demo", False)
        valid = (
            isinstance(customer_id, str)
            and CUSTOMER_ID_PATTERN.fullmatch(customer_id)
            and isinstance(session_id, str)
            and bool(session_id)
            and isinstance(demo, bool)
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
        return Principal(customer_id, session_id, issued_at, expires_at, kid, demo)

    def revoke(self, principal: Principal) -> None:
        """Revoke the session of ``principal`` until it would have expired."""
        self._revocations.revoke(principal.session_id, principal.expires_at, self._clock())
