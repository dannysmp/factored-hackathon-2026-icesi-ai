"""
Demonstration Sign-In Broker
=============================

Overview
--------
A public, persona-based sign-in for the deployed demonstration: the sandbox login's
shared key must never reach a browser, and the deployment runs as production, where the sandbox
login is refused — so a public demonstration has no working sign-in without a server-side path.
This route is that path, deliberately narrower than the sandbox login it stands in for: it accepts
only a persona slug from a small, versioned, seed-validated list, never a customer identifier or a
document number.

Scope
-----
In: the three routes (customer sign-in, agent sign-in, and the read-only persona directory both
pickers use), their request and response shapes, and wiring the access-code check, the persona
lookup, the issuance limits and the sign-in audit together: the access code is compared first,
then the persona is looked up, the issuance reservations are taken, and the attempt is audited
before a token is returned. The two
sign-in brokers share every helper below but are never the same route or the same access code, so
a leaked customer code leaves the console protected. The persona directory
carries no access code of its own — it mints no session and reveals nothing beyond a slug, a
display name, a language and which audience it belongs to (never a customer or agent id) — but is
gated by the same two settings, so the kill switch hides it exactly like the two sign-in routes.
Out: validating the persona file or checking it against the seed (``app.security.demo_personas``,
done once at start-up — the agent list has no seed to check against), issuing or verifying the
token itself (``SessionService``), the two limiter implementations (``app.security.limits``,
``app.security.issuance_limits``), the console's own routes (queue, packet, audit timeline —
``app.api.agent``; this module only gets an agent a token).

Design Principles
------------------
- **The access code is compared before anything is counted**, exactly like the sandbox login's own
  check: a correct code always proceeds regardless of this
  address's recorded failures, and only a wrong code counts against `AttemptLimiter` — a
  failure-only counter never reused for the issuance caps below.
- **Every attempt is audited before a token is returned, refusals included**: a write
  that cannot complete fails the whole sign-in closed, as a service failure, never a silent
  refusal or a token issued without a record of it.
- **One uniform refusal** for a wrong access code, an unknown persona and a rate limit: the same
  code, status and message, so a caller cannot use the response shape to learn which check failed
  or whether a given persona slug exists.
- **The issuance caps run only after the persona is known to be valid**, and count the success,
  not the attempt: a caller who never gets past the access code or the persona lookup never
  consumes issuance capacity meant for real demo sessions.

Runtime Contract
-----------------
``POST /v1/auth/demo-sessions``  body ``{"persona": str}``, header ``X-Demo-Access-Code``  ->
201 session (public while ``DEMO_SIGNIN_ENABLED`` is true).
``POST /v1/auth/demo-agent-sessions``  body ``{"persona": str}``, header ``X-Demo-Access-Code``  ->
201 session (public while ``DEMO_AGENT_SIGNIN_ENABLED`` is true), its own access code.
``GET /v1/auth/demo-personas``  -> 200 ``{"personas": [{slug, display_name, language, audience}]}``
(public whenever either kill switch is on; customer personas only, agent personas only, or both,
matching which switch is on).

Limitations
-----------
An issued agent session is accepted only under the ``/v1/agent`` audience prefix, where the
console's own routes (``app.api.agent``) live. Every other protected route, including
``/v1/session``, stays customer-only and refuses an agent token.
"""

from __future__ import annotations

# Standard libraries
import hashlib  # Client address hashed before it reaches the audit record
import hmac  # Constant-time comparison of the shared access code
import logging  # Structured events about demo sign-ins
import re  # Strict persona-slug pattern
from datetime import datetime, timedelta  # Session lifetimes and reservation TTLs
from typing import Annotated, Literal  # Header and field declarations, closed audience values

# Third-party libraries
from fastapi import APIRouter, Depends, Header, Request  # Routing and request access
from pydantic import BaseModel, ConfigDict, Field, SecretStr  # Validated models

# Local modules
from app.api.auth import SessionResponse  # Shared response shape with the sandbox login
from app.security.client_address import client_address  # The real connecting address
from app.security.demo_personas import PersonaList  # The validated, seed-checked persona list
from app.security.errors import ErrorCode, ProblemError  # Failure format
from app.security.issuance_limits import IssuanceLimiter  # Concurrent-session caps
from app.security.limits import AttemptLimiter  # Failed-access-code limit
from app.security.middleware import current_request_id  # Request identifier for logs
from app.security.sessions import SessionService  # Sessions
from app.security.signin_audit import (  # The sign-in audit record and sink
    SignInAudience,
    SignInAuditRecord,
    SignInAuditSink,
    SignInOutcome,
    SignInReasonCode,
)

logger = logging.getLogger(__name__)

DEMO_SESSIONS_PATH = "/v1/auth/demo-sessions"
AGENT_SESSIONS_PATH = "/v1/auth/demo-agent-sessions"
DEMO_PERSONAS_PATH = "/v1/auth/demo-personas"
CUSTOMER_TTL = timedelta(minutes=30)
AGENT_TTL = timedelta(minutes=60)

# Bound how many live demo sessions one address, the whole broker, and one persona slot may hold
# at once; the limits count successes, not attempts. A persona is one demo identity; more than
# one live session under it risks duplicate open cases for customers and a confusing shared queue
# view for agents. The same defaults serve both brokers; each gets
# its own ``IssuanceLimiter`` instance, so the caps never share counters across audiences.
# The address cap covers the customer persona roster (5, personas/demo_personas_v1.yaml) with no
# headroom to spare against the smaller agent roster (2): one real visitor stepping through every
# customer persona in one sitting -- a real evaluation session's own shape -- must not be refused
# by its own earlier, still-live sessions before reaching the last one.
DEFAULT_ADDRESS_CAP = 5
DEFAULT_GLOBAL_CAP = 50
DEFAULT_PERSONA_CAP = 1

PERSONA_SLUG_PATTERN = re.compile(r"[a-z0-9-]{1,32}")
_ANCHORED_PERSONA_SLUG = f"^{PERSONA_SLUG_PATTERN.pattern}$"

_UNIFORM_REFUSAL_TITLE = "Sign-in was refused"


class DemoSignInRequest(BaseModel):
    """Body of the demo sign-in: a persona slug, and nothing else.

    Never a customer identifier or a document number — the customer_id a slug maps to
    never appears on the wire in either direction.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    persona: Annotated[str, Field(min_length=1, max_length=32, pattern=_ANCHORED_PERSONA_SLUG)]


class DemoPersonaSummary(BaseModel):
    """One persona as the directory shows it: enough to pick it, nothing that identifies who it
    maps to — no ``customer_id``/``agent_id``, no ``scenario``/``specialty``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str
    display_name: str
    language: str
    audience: Literal["customer", "agent"]


class DemoPersonaDirectory(BaseModel):
    """The whole listing the sign-in screen's picker renders."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    personas: tuple[DemoPersonaSummary, ...]


def _address_hash(address: str) -> str:
    """A stable, non-reversible key for the audit record; the raw address is never stored."""
    return hashlib.sha256(address.encode("utf-8")).hexdigest()


def _refusal() -> ProblemError:
    """The one refusal shape for a wrong access code, an unknown persona or a hit rate limit."""
    return ProblemError(
        ErrorCode.DEMO_SIGNIN_REJECTED, 401, _UNIFORM_REFUSAL_TITLE, reauth_required=True
    )


def _reserve_all(
    limiter: IssuanceLimiter, keys_and_caps: list[tuple[str, int]], ttl: timedelta
) -> list[tuple[str, datetime]] | None:
    """Reserve every key in order, or release whatever already succeeded and refuse.

    Reserving three independent keys is not one atomic operation across all three, so a caller
    that stopped after the first success would leave it burning capacity for an attempt that
    never completed; this releases every earlier success as soon as one key refuses. On success,
    returns every ``(key, expiry)`` reserved, so the caller can release them too if a later step
    of the same attempt — issuing the token, writing its audit record — fails after all three
    reservations already succeeded.
    """
    reserved: list[tuple[str, datetime]] = []
    for key, cap in keys_and_caps:
        expiry = limiter.try_reserve(key, cap, ttl)
        if expiry is None:
            for done_key, done_expiry in reserved:
                limiter.release(done_key, done_expiry)
            return None
        reserved.append((key, expiry))
    return reserved


def _release_all(limiter: IssuanceLimiter, reserved: list[tuple[str, datetime]]) -> None:
    """Undo every reservation in ``reserved`` (the attempt they were held for did not complete)."""
    for key, expiry in reserved:
        limiter.release(key, expiry)


def _rate_limited(wait: int) -> ProblemError:
    """The refusal for a limiter hit — its own code, since ``Retry-After`` already says which."""
    return ProblemError(
        ErrorCode.TOO_MANY_ATTEMPTS,
        429,
        "Too many attempts",
        "Wait before trying again.",
        headers={"Retry-After": str(wait)},
    )


def build_demo_signin_router(
    *,
    sessions: SessionService,
    demo_access_code: SecretStr,
    personas: PersonaList,
    audit: SignInAuditSink,
    attempt_limiter: AttemptLimiter,
    issuance_limiter: IssuanceLimiter,
    address_cap: int = DEFAULT_ADDRESS_CAP,
    global_cap: int = DEFAULT_GLOBAL_CAP,
    persona_cap: int = DEFAULT_PERSONA_CAP,
) -> APIRouter:
    """Build the demonstration sign-in route.

    Parameters
    ----------
    sessions : SessionService
        Issues session tokens; must hold a signing key for the ``"customer"`` audience.
    demo_access_code : SecretStr
        The shared secret this broker requires, compared in constant time.
    personas : PersonaList
        Already loaded and validated against the seed (``app.security.demo_personas``) before
        this router is built — this module trusts every persona it is given resolves correctly.
    audit : SignInAuditSink
        Where every attempt, refused included, is recorded before a token is ever returned.
    attempt_limiter : AttemptLimiter
        Limits wrong access codes per client address; never reused for issuance capacity.
    issuance_limiter : IssuanceLimiter
        Bounds concurrent successful sign-ins per address, globally and per persona slot.
    """
    expected = demo_access_code.get_secret_value().encode("utf-8")
    router = APIRouter()

    def _audit_or_fail_closed(entry: SignInAuditRecord) -> None:
        """Write the audit record or fail the whole attempt closed."""
        try:
            audit.record(entry)
        except Exception as error:
            logger.error("signin_audit_failed request_id=%s", current_request_id())
            raise ProblemError(
                ErrorCode.SERVICE_UNAVAILABLE,
                503,
                "The sign-in could not be completed",
                "Try again shortly.",
                headers={"Retry-After": "60"},
            ) from error

    def authorize_demo_client(
        request: Request,
        x_demo_access_code: Annotated[str | None, Header()] = None,
    ) -> None:
        """Refuse the demo sign-in unless the caller holds the access code.

        Runs before the body is validated, so an unauthenticated caller learns nothing about the
        body's shape. The supplied code is compared, in constant time, before anything is
        counted, matching the sandbox login's own check: a correct code always
        proceeds regardless of this address's recorded failures, and only a wrong code counts
        against the limiter.
        """
        supplied = (x_demo_access_code or "").encode("utf-8")
        if hmac.compare_digest(supplied, expected):
            return
        address = client_address(request)
        wait = attempt_limiter.begin_attempt(address)
        record = SignInAuditRecord(
            trace_id=current_request_id(),
            occurred_at=sessions.now(),
            audience=SignInAudience.CUSTOMER,
            outcome=SignInOutcome.REFUSED,
            reason_code=(
                SignInReasonCode.RATE_LIMITED if wait else SignInReasonCode.INVALID_ACCESS_CODE
            ),
            client_address_hash=_address_hash(address),
        )
        if wait:
            logger.warning("demo_signin_limited request_id=%s", current_request_id())
            _audit_or_fail_closed(record)
            raise _rate_limited(wait)
        logger.warning("demo_signin_rejected request_id=%s", current_request_id())
        _audit_or_fail_closed(record)
        raise _refusal()

    @router.post(DEMO_SESSIONS_PATH, status_code=201, dependencies=[Depends(authorize_demo_client)])
    def create_demo_session(body: DemoSignInRequest, request: Request) -> SessionResponse:
        """Issue a demo session for a known persona, or refuse."""
        address = client_address(request)
        address_hash = _address_hash(address)
        persona = personas.customer_by_slug(body.persona)
        if persona is None:
            logger.warning("demo_signin_unknown_persona request_id=%s", current_request_id())
            _audit_or_fail_closed(
                SignInAuditRecord(
                    trace_id=current_request_id(),
                    occurred_at=sessions.now(),
                    audience=SignInAudience.CUSTOMER,
                    outcome=SignInOutcome.REFUSED,
                    reason_code=SignInReasonCode.UNKNOWN_PERSONA,
                    client_address_hash=address_hash,
                    persona_slug=body.persona,
                )
            )
            raise _refusal()

        reserved = _reserve_all(
            issuance_limiter,
            [
                (f"address:{address}", address_cap),
                ("global:customer", global_cap),
                (f"persona:{persona.slug}", persona_cap),
            ],
            CUSTOMER_TTL,
        )
        if reserved is None:
            logger.warning("demo_signin_capacity_reached request_id=%s", current_request_id())
            _audit_or_fail_closed(
                SignInAuditRecord(
                    trace_id=current_request_id(),
                    occurred_at=sessions.now(),
                    audience=SignInAudience.CUSTOMER,
                    outcome=SignInOutcome.REFUSED,
                    reason_code=SignInReasonCode.RATE_LIMITED,
                    client_address_hash=address_hash,
                    persona_slug=persona.slug,
                )
            )
            raise _rate_limited(60)

        issued = sessions.issue(
            persona.customer_id, audience="customer", ttl=CUSTOMER_TTL, demo=True
        )
        try:
            _audit_or_fail_closed(
                SignInAuditRecord(
                    trace_id=current_request_id(),
                    occurred_at=sessions.now(),
                    audience=SignInAudience.CUSTOMER,
                    outcome=SignInOutcome.ISSUED,
                    reason_code=SignInReasonCode.ISSUED,
                    client_address_hash=address_hash,
                    persona_slug=persona.slug,
                    resolved_customer_id=persona.customer_id,
                    session_id=issued.session_id,
                )
            )
        except ProblemError:
            # No token reaches the caller on this path (it fails closed on the audit write),
            # so the three reservations above must not either — otherwise one audit-store hiccup
            # would burn a persona's only slot for up to its full TTL despite no session ever
            # being delivered, the same self-inflicted lockout the caps exist to prevent.
            _release_all(issuance_limiter, reserved)
            raise
        logger.info(
            "demo_session_issued session_id=%s request_id=%s",
            issued.session_id,
            current_request_id(),
        )
        ttl_seconds = int((issued.expires_at - sessions.now()).total_seconds())
        return SessionResponse(
            access_token=issued.token, expires_at=issued.expires_at, expires_in=ttl_seconds
        )

    return router


def build_demo_agent_signin_router(
    *,
    sessions: SessionService,
    demo_access_code: SecretStr,
    personas: PersonaList,
    audit: SignInAuditSink,
    attempt_limiter: AttemptLimiter,
    issuance_limiter: IssuanceLimiter,
    address_cap: int = DEFAULT_ADDRESS_CAP,
    global_cap: int = DEFAULT_GLOBAL_CAP,
    persona_cap: int = DEFAULT_PERSONA_CAP,
) -> APIRouter:
    """Build the agent demonstration sign-in route.

    Mirrors ``build_demo_signin_router`` exactly, audience by audience: its own access code (never
    the customer one — a leaked customer code must not expose the console), its own persona list
    (``personas.agents``), its own TTL (60 minutes, the agent bound) and its own resolved-id
    audit field (``resolved_agent_id``, never ``resolved_customer_id``). The two brokers never
    share an ``AttemptLimiter`` or ``IssuanceLimiter`` instance, so a run on one never counts
    against the other.

    Parameters
    ----------
    sessions : SessionService
        Issues session tokens; must hold a signing key for the ``"agent"`` audience.
    demo_access_code : SecretStr
        The agent broker's own shared secret, compared in constant time.
    personas : PersonaList
        Already loaded (``app.security.demo_personas``); agent personas are not checked against
        any seed, since they resolve to no row (unlike customers).
    audit : SignInAuditSink
        Where every attempt, refused included, is recorded before a token is ever returned.
    attempt_limiter : AttemptLimiter
        Limits wrong access codes per client address for this broker only.
    issuance_limiter : IssuanceLimiter
        Bounds concurrent successful sign-ins per address, globally and per persona slot, for this
        broker only.
    """
    expected = demo_access_code.get_secret_value().encode("utf-8")
    router = APIRouter()

    def _audit_or_fail_closed(entry: SignInAuditRecord) -> None:
        """Write the audit record or fail the whole attempt closed."""
        try:
            audit.record(entry)
        except Exception as error:
            logger.error("signin_audit_failed request_id=%s", current_request_id())
            raise ProblemError(
                ErrorCode.SERVICE_UNAVAILABLE,
                503,
                "The sign-in could not be completed",
                "Try again shortly.",
                headers={"Retry-After": "60"},
            ) from error

    def authorize_demo_agent_client(
        request: Request,
        x_demo_access_code: Annotated[str | None, Header()] = None,
    ) -> None:
        """Refuse the agent demo sign-in unless the caller holds the agent access code."""
        supplied = (x_demo_access_code or "").encode("utf-8")
        if hmac.compare_digest(supplied, expected):
            return
        address = client_address(request)
        wait = attempt_limiter.begin_attempt(address)
        record = SignInAuditRecord(
            trace_id=current_request_id(),
            occurred_at=sessions.now(),
            audience=SignInAudience.AGENT,
            outcome=SignInOutcome.REFUSED,
            reason_code=(
                SignInReasonCode.RATE_LIMITED if wait else SignInReasonCode.INVALID_ACCESS_CODE
            ),
            client_address_hash=_address_hash(address),
        )
        if wait:
            logger.warning("demo_agent_signin_limited request_id=%s", current_request_id())
            _audit_or_fail_closed(record)
            raise _rate_limited(wait)
        logger.warning("demo_agent_signin_rejected request_id=%s", current_request_id())
        _audit_or_fail_closed(record)
        raise _refusal()

    @router.post(
        AGENT_SESSIONS_PATH, status_code=201, dependencies=[Depends(authorize_demo_agent_client)]
    )
    def create_demo_agent_session(body: DemoSignInRequest, request: Request) -> SessionResponse:
        """Issue an agent demo session for a known persona, or refuse."""
        address = client_address(request)
        address_hash = _address_hash(address)
        persona = personas.agent_by_slug(body.persona)
        if persona is None:
            logger.warning("demo_agent_signin_unknown_persona request_id=%s", current_request_id())
            _audit_or_fail_closed(
                SignInAuditRecord(
                    trace_id=current_request_id(),
                    occurred_at=sessions.now(),
                    audience=SignInAudience.AGENT,
                    outcome=SignInOutcome.REFUSED,
                    reason_code=SignInReasonCode.UNKNOWN_PERSONA,
                    client_address_hash=address_hash,
                    persona_slug=body.persona,
                )
            )
            raise _refusal()

        reserved = _reserve_all(
            issuance_limiter,
            [
                (f"address:{address}", address_cap),
                ("global:agent", global_cap),
                (f"persona:{persona.slug}", persona_cap),
            ],
            AGENT_TTL,
        )
        if reserved is None:
            logger.warning("demo_agent_signin_capacity_reached request_id=%s", current_request_id())
            _audit_or_fail_closed(
                SignInAuditRecord(
                    trace_id=current_request_id(),
                    occurred_at=sessions.now(),
                    audience=SignInAudience.AGENT,
                    outcome=SignInOutcome.REFUSED,
                    reason_code=SignInReasonCode.RATE_LIMITED,
                    client_address_hash=address_hash,
                    persona_slug=persona.slug,
                )
            )
            raise _rate_limited(60)

        issued = sessions.issue(persona.agent_id, audience="agent", ttl=AGENT_TTL, demo=True)
        try:
            _audit_or_fail_closed(
                SignInAuditRecord(
                    trace_id=current_request_id(),
                    occurred_at=sessions.now(),
                    audience=SignInAudience.AGENT,
                    outcome=SignInOutcome.ISSUED,
                    reason_code=SignInReasonCode.ISSUED,
                    client_address_hash=address_hash,
                    persona_slug=persona.slug,
                    resolved_agent_id=persona.agent_id,
                    session_id=issued.session_id,
                )
            )
        except ProblemError:
            # Same reservation-leak fix as the customer broker: no token reaches the caller on
            # this path, so the three reservations above must not either.
            _release_all(issuance_limiter, reserved)
            raise
        logger.info(
            "demo_agent_session_issued session_id=%s request_id=%s",
            issued.session_id,
            current_request_id(),
        )
        ttl_seconds = int((issued.expires_at - sessions.now()).total_seconds())
        return SessionResponse(
            access_token=issued.token, expires_at=issued.expires_at, expires_in=ttl_seconds
        )

    return router


def build_demo_persona_directory_router(
    *,
    personas: PersonaList,
    include_customers: bool,
    include_agents: bool,
    attempt_limiter: AttemptLimiter,
) -> APIRouter:
    """Build the read-only persona directory a sign-in picker renders.

    Unauthenticated by design: the access codes bound *issuance* (spend and session risk),
    not read access to a non-sensitive persona list, and this route mints no session and reveals
    no customer or agent identifier. Rate-limited all the same, against the same abuse surface
    every other public route on this host faces, using ``attempt_limiter`` as a plain per-address
    request counter — every call counts, not only a failure, since a listing route has no
    separate notion of "wrong".

    Parameters
    ----------
    personas : PersonaList
        Already loaded (``app.security.demo_personas``).
    include_customers : bool
        List customer personas — pass the same value as ``DEMO_SIGNIN_ENABLED`` so a caller can
        never discover personas for a broker that is off.
    include_agents : bool
        List agent personas — pass the same value as ``DEMO_AGENT_SIGNIN_ENABLED``, for the same
        reason.
    attempt_limiter : AttemptLimiter
        This route's own instance — never one of the two sign-in brokers' own limiters, which
        count wrong access codes, a different contract.
    """
    router = APIRouter()

    @router.get(DEMO_PERSONAS_PATH)
    def list_demo_personas(request: Request) -> DemoPersonaDirectory:
        """List the personas the enabled broker(s) will accept, never who they map to."""
        wait = attempt_limiter.begin_attempt(client_address(request))
        if wait:
            logger.warning("demo_persona_directory_limited request_id=%s", current_request_id())
            raise _rate_limited(wait)
        summaries: list[DemoPersonaSummary] = []
        if include_customers:
            summaries.extend(
                DemoPersonaSummary(
                    slug=persona.slug,
                    display_name=persona.display_name,
                    language=persona.language,
                    audience="customer",
                )
                for persona in personas.customers
            )
        if include_agents:
            summaries.extend(
                # An agent may speak more than one language; the directory's own `language` field
                # is singular so the picker shows one row per persona, not one per language it
                # speaks — the first language in the list is that persona's primary one.
                DemoPersonaSummary(
                    slug=persona.slug,
                    display_name=persona.display_name,
                    language=persona.languages[0],
                    audience="agent",
                )
                for persona in personas.agents
            )
        return DemoPersonaDirectory(personas=tuple(summaries))

    return router
