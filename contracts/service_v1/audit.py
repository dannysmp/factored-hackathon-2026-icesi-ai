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
Out: the store that makes a record append-only (a database constraint in the serving store), and
reading a timeline back (the case service and the console).

Design Principles
-----------------
- Two clocks, both required: the real UTC instant the action occurred, and the domain date in
  force when it did. Session and audit code read only the real clock; the domain date reaches
  them as a value passed in, never read from the domain calendar directly, so an audit timestamp
  can never be back-dated by a setting.
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
Append-only is enforced at the store; this contract shapes the record, not the guarantee that
one, once written, cannot be changed or removed.
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

    ``TRANSACTION_PROBED`` and ``CASE_PROBED``: a reference call whose reference exists but
    belongs to another customer answers the session's customer exactly as a genuine not-found
    would (same code, message and shape), but is audited distinguishably from one, so enumeration
    is visible in the trail even though it is invisible to the caller.
    ``CASE_CREATION_REPLAYED``: a repeated confirmation with the same idempotency key and payload
    answers the customer exactly as the original filing did, but the trail still shows a distinct
    entry for it, so a trace built from audit records alone accounts for every filing call the
    customer actually made.
    ``PACKET_VIEWED`` and ``TIMELINE_VIEWED``: an agent opening a handoff packet or a
    conversation's audit timeline is a customer-data access like any other, so it is audited under
    the same fail-closed rule — ``customer_id`` names the customer whose packet or timeline was
    opened, and ``session_id`` the agent's own session, never a customer one.
    ``TICKET_CLAIMED``, ``TICKET_RELEASED``, ``TICKET_NOTE_ADDED`` and ``CASE_STATUS_SET`` are the
    narrow agent writes: each carries ``agent_id`` the same way the two read actions above do,
    since a session alone cannot answer who made the write once it expires.
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
    TICKET_CLAIMED = "ticket_claimed"
    TICKET_RELEASED = "ticket_released"
    TICKET_NOTE_ADDED = "ticket_note_added"
    CASE_STATUS_SET = "case_status_set"


class AuditRecord(ContractModel):
    """One append-only entry: who did what, on what basis, and when.

    Written for every user-affecting decision. ``reason_code`` and ``policy_version`` are set
    exactly for the actions a policy decision produced (``dispute_evaluated``, ``case_created``,
    ``case_creation_refused`` for a decision the tool itself rejected, and
    ``case_creation_replayed`` for the decision the original filing rested on); a plain read of
    the customer's own data carries neither. ``agent_id`` is set exactly when an agent, not a
    customer, is the one who acted (every agent read or write is audited with the agent's own
    identity, not only the session that carried it) — ``None`` for every customer-originated
    action, where ``session_id`` alone already identifies the actor.
    """

    # Ties the record to one conversation; every record of that conversation shares it.
    trace_id: Annotated[str, Field(pattern=NUMBER_PATTERN)]
    # The customer whose data the action touched, by internal identifier.
    customer_id: Annotated[SafeText, Field(min_length=1, max_length=20)]
    # The session that carried the action: the customer's, or the agent's for an agent action.
    session_id: Annotated[SafeText, Field(min_length=1, max_length=64)]
    action: AuditAction
    reason_code: ReasonCode | None = None
    policy_version: Annotated[str, Field(min_length=1)] | None = None
    # Lowercase SHA-256 hex digest of the tool result: identifies it without holding its content.
    tool_result_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    # The real UTC instant of the action, and the domain date in force when it happened.
    occurred_at: UtcDatetime
    domain_date: date
    agent_id: Annotated[SafeText, Field(min_length=1, max_length=20)] | None = None


# -----------------------------------------------------------------------------
# Port
# -----------------------------------------------------------------------------


class AuditSink(Protocol):
    """Where a tool call writes its audit record; never read from here, only written."""

    def record(self, entry: AuditRecord) -> None:
        """Append ``entry``.

        Implementations fail closed: a record that cannot be written is an error the caller must
        not swallow, not a dropped entry.
        """
