"""
Sign-In Audit
=============

Overview
--------
One record per demo sign-in attempt (ADR-18): the fact that an attempt happened, its outcome, and
enough to reconstruct why — before any session exists, which is what makes this a different kind
of record from ``contracts.service_v1.audit``'s ``AuditRecord`` (scoped to a tool call, always
inside an established session). Written before a token is ever returned; a write that cannot
complete fails the sign-in closed (``SignInAuditSink``'s own contract, mirroring ``AuditSink``).

Scope
-----
In: the record shape and the sink port.
Out: the Postgres implementation (``app.persistence.signin_audit``), deciding when an attempt
happens (the broker route), hashing the client address (the broker route, so this module never
sees a raw one).

Design Principles
------------------
- Never a customer identifier unless a real, seeded customer actually resolved — no placeholder:
  ``persona_slug`` and ``resolved_customer_id`` are independently optional.
- Never the access code, in any field.
- Fails closed: ``SignInAuditSink.record`` raising propagates; nothing here swallows it into a
  dropped entry.

Runtime Contract
-----------------
``SignInAuditRecord``; ``SignInAuditSink.record(entry) -> None``.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass  # Immutable audit record
from datetime import datetime  # Real UTC instant only (never the domain calendar)
from enum import StrEnum  # Closed sets of this record's own fields
from typing import Protocol  # Interface of the sink


class SignInAudience(StrEnum):
    """Which broker the attempt was made against."""

    CUSTOMER = "customer"
    AGENT = "agent"


class SignInOutcome(StrEnum):
    """Whether the attempt ended in a session or a refusal."""

    ISSUED = "issued"
    REFUSED = "refused"


class SignInReasonCode(StrEnum):
    """Why a sign-in attempt ended as it did. Codes are stable: added, never renamed."""

    ISSUED = "issued"
    INVALID_ACCESS_CODE = "invalid_access_code"
    UNKNOWN_PERSONA = "unknown_persona"
    INACTIVE_CUSTOMER = "inactive_customer"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True, slots=True)
class SignInAuditRecord:
    """One demo sign-in attempt, before or instead of a session existing.

    ``session_id`` is present exactly when ``outcome`` is ``ISSUED`` (enforced at the store too,
    migration 0006); ``persona_slug`` and ``resolved_customer_id`` are set only as far as the
    attempt actually got before it was decided.
    """

    trace_id: str
    occurred_at: datetime
    audience: SignInAudience
    outcome: SignInOutcome
    reason_code: SignInReasonCode
    client_address_hash: str
    persona_slug: str | None = None
    resolved_customer_id: str | None = None
    session_id: str | None = None


class SignInAuditSink(Protocol):
    """Where every demo sign-in attempt goes, append-only."""

    def record(self, entry: SignInAuditRecord) -> None:
        """Append ``entry``; raises rather than swallowing a failed write."""
