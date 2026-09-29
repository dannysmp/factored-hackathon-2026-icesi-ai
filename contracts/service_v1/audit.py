"""
Audit Record Contract, Service Version 1
=========================================

Overview
--------
The single append-only record of a user-affecting decision or action: who did it, what it was,
the reason and policy version behind it, a hash of the tool result, the trace that ties it to one
conversation, and both clocks. Nothing here is enough to reconstruct a conversation; it is enough
to answer, for one action, who did it, on what basis, and when.

Scope
-----
In: the audit record, its closed set of actions, and the sink port a tool call writes it to.
Out: the store that makes a record append-only (a database constraint, from the serving-store
slice on), and reading a timeline back (the case service and console, later slices).

Design Principles
-----------------
- Two clocks, both required: the real UTC instant the action occurred, and the domain date in
  force when it did (ADR-15). Session and audit code read only the real clock; the domain date
  reaches them as a value passed in, never read from the domain calendar directly, so an audit
  timestamp can never be back-dated by a setting.
- Only what the invariant needs. A reason code and a policy version are recorded exactly when a
  policy decision produced the action; a read that carries neither still identifies who read what.
- No raw material: a document number, a full card number, a name or a message's own text is never
  a field of this record. What identifies a tool result is its hash, not its content.
- The models are immutable and reject unknown fields, so a misspelled key fails at the boundary.

Runtime Contract
-----------------
``AuditRecord`` with ``AuditAction``; the port ``AuditSink``.

Limitations
-----------
Append-only is enforced at the store, starting with the serving-store slice; this contract shapes
the record, not the guarantee that one, once written, cannot be changed or removed.
"""

from __future__ import annotations

# Standard libraries
from datetime import date  # The domain date in force when the action occurred
from enum import StrEnum  # Closed sets of the contract
from typing import Annotated, Protocol  # Bounded fields and the port interface

# Third-party libraries
from pydantic import Field  # Field bounds

# Local modules
from app.domain.policy.models import ReasonCode  # One vocabulary, not redefined
from contracts.service_v1.cases import NUMBER_PATTERN, ContractModel, SafeText, UtcDatetime

# -----------------------------------------------------------------------------
# Record
# -----------------------------------------------------------------------------


class AuditAction(StrEnum):
    """What happened. One entry per tool call that decides, acts, or is asked to and cannot.

    ``TRANSACTION_PROBED`` and ``CASE_PROBED`` are a compatible addition after this contract
    froze: a reference call whose reference exists but belongs to another customer answers the
    session's customer exactly as a genuine not-found would (same code, message and shape), but
    is audited distinguishably from one, so enumeration is visible in the trail even though it is
    invisible to the caller. ``CASE_CREATION_REPLAYED`` is the same kind of addition: a repeated
    confirmation with the same idempotency key and payload answers the customer exactly as the
    original filing did, but the trail still shows a distinct entry for it, so a trace built from
    audit records alone accounts for every filing call the customer actually made.
    ``PACKET_VIEWED`` and ``TIMELINE_VIEWED`` are a further compatible addition: an agent opening a
    handoff packet or a conversation's audit timeline is a customer-data access like any other
    (ADR-17), so it is audited under the same fail-closed rule — ``customer_id`` names the
    customer whose packet or timeline was opened, and ``session_id`` the agent's own session,
    never a customer one.
    """

    TRANSACTIONS_LISTED = "transactions_listed"
    TRANSACTION_VIEWED = "transaction_viewed"
    TRANSACTION_PROBED = "transaction_probed"
    CASES_LISTED = "cases_listed"
    CASE_VIEWED = "case_viewed"
    CASE_PROBED = "case_probed"
    DISPUTE_EVALUATED = "dispute_evaluated"
    CASE_CREATED = "case_created"
    CASE_CREATION_REFUSED = "case_creation_refused"
    CASE_CREATION_REPLAYED = "case_creation_replayed"
    PACKET_VIEWED = "packet_viewed"
    TIMELINE_VIEWED = "timeline_viewed"


class AuditRecord(ContractModel):
    """One append-only entry: who did what, on what basis, and when.

    Written for every user-affecting decision. ``reason_code`` and ``policy_version`` are set
    exactly for the actions a policy decision produced (``dispute_evaluated``, ``case_created``,
    ``case_creation_refused`` for a decision the tool itself rejected, and
    ``case_creation_replayed`` for the decision the original filing rested on); a plain read of
    the customer's own data carries neither.
    """

    trace_id: Annotated[str, Field(pattern=NUMBER_PATTERN)]
    customer_id: Annotated[SafeText, Field(min_length=1, max_length=20)]
    session_id: Annotated[SafeText, Field(min_length=1, max_length=64)]
    action: AuditAction
    reason_code: ReasonCode | None = None
    policy_version: Annotated[str, Field(min_length=1)] | None = None
    tool_result_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    occurred_at: UtcDatetime
    domain_date: date


# -----------------------------------------------------------------------------
# Port
# -----------------------------------------------------------------------------


class AuditSink(Protocol):
    """Where a tool call writes its audit record; never read from here, only written."""

    def record(self, entry: AuditRecord) -> None:
        """Append ``entry``. Implementations fail closed: a record that cannot be written is an
        error the caller must not swallow, not a dropped entry."""
