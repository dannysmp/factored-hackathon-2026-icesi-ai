"""
Postgres Agent Writes
=====================

Overview
--------
The console's narrow agent writes: claim or release a handoff ticket, add a note, and set a
case's status among Open, In Review, Resolved and Rejected. Each write is audited with the
agent's own identity, the same requirement the console's reads already carry
(``app.persistence.console_audit``), and each result is read back from the store before it is
returned.

Scope
-----
In: ``PostgresAgentWrites.claim_ticket``/``release_ticket``/``add_note``/``set_case_status``.
Out: reading a ticket, a case or a queue row (``app.persistence.handoff_queue``,
``.ticket_detail``, ``.reads``); the routes that call this (``app.api.agent``); deciding what a
write may state at the contract level (``contracts.service_v1.console`` shapes the request; this
module enforces what it actually allows).

Design Principles
-----------------
- **The mutation and its audit write are two separate store connections, not one atomic
  transaction** (matching ``app.persistence.reads``'s ``_insert_case`` and
  ``app.persistence.audit``'s one-connection-per-call design): the audit sink's ``record`` call
  opens and commits on its own connection before the mutation's own connection commits, so a
  process crash in the narrow window between the two could leave an audit record for a write that
  never actually took effect. The same limitation applies to case creation, for the same reason.
- **A case status write only ever ``UPDATE``s, never ``INSERT``s:** this module has no statement
  that could create a case row.
- **Resolved and Rejected are terminal.** Once a case reaches either one, a further status-set is
  refused (``CaseStatusTerminal``): the case lifecycle only ever moves Open/In Review toward a
  resolution, never backward through it and never sideways between the two terminal states,
  matching ``CaseStatus``'s own documented direction. Setting a status to its own current value
  (while not terminal) is a harmless, audited no-op, not a special case.
- **A note is never edited or removed once added** (``contracts.service_v1.console.Note``); this
  module only ever appends one, in the same ``(ticket_ref, ord)`` shape every other bounded
  repeating part of a handoff uses.
- **One connection per call**, matching every other module in ``app.persistence``.
- **Fails closed:** a write that cannot complete raises; nothing here swallows a
  ``psycopg.Error`` into a silently skipped write. A missing ticket or case is a different
  thing: a deliberate ``None`` return, matching the not-found contract of
  ``app.persistence.ticket_detail.PostgresTicketDetail`` (``app.api.agent``'s
  ``TicketDetailPort``), never an exception, since the caller has one thing to do with it either
  way (answer 404) and no recovery path a raised exception would suit better.
- **A terminal case status is refused with an exception, not a sentinel:** unlike a missing
  ticket or case, it is not "nothing to report". The case was found, and the request is refused
  because of the state it is already in, the same distinction ``app.conversation.store``'s
  ``Conflict`` draws for a conversation that moved on. ``CaseStatusTerminal`` is the one
  exception this module expects its caller to import and translate.

Runtime Contract
----------------
``PostgresAgentWrites(dsn, *, sink, queue, calendar, clock)`` with ``claim_ticket(...)``,
``release_ticket(...)``, ``add_note(...)``, ``set_case_status(...)``, each returning ``None`` for
a ticket or case that does not exist. ``CaseStatusTerminal``.
"""

from __future__ import annotations

# Standard libraries
import hashlib  # Canonical hash of what a write actually changed
import json  # Canonical JSON form to hash
from dataclasses import dataclass

# Third-party libraries
import psycopg  # Serving-store driver

# Local modules
from app.domain.calendar import DomainCalendar
from app.persistence.handoff_queue import PostgresHandoffQueue
from app.security.sessions import Clock
from contracts.service_v1.audit import AuditAction, AuditRecord, AuditSink
from contracts.service_v1.cases import CaseStatus
from contracts.service_v1.console import CaseStatusResult, Note, QueueItem

_CONNECT_TIMEOUT_SECONDS = 5

_TERMINAL_STATUSES = frozenset({CaseStatus.RESOLVED, CaseStatus.REJECTED})


class CaseStatusTerminal(Exception):
    """The case is already Resolved or Rejected; a further status-set is refused.

    The exception argument is the case number.
    """


def _hash(payload: object) -> str:
    """SHA-256 of ``payload``'s canonical JSON form, the digest every audited write records as
    its result hash (an independent copy of the helper in ``app.persistence.console_audit``,
    since each module's helper is private)."""
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _TicketIdentity:
    """The columns of a ``handoff_outbox`` row an agent write needs: whose ticket it is, the
    conversation trace it belongs to, and the filed case it points at, if any."""

    customer_id: str
    trace_id: str
    existing_case_number: str | None


class PostgresAgentWrites:
    """Writes the four narrow agent actions, each audited with the agent's own identity."""

    def __init__(
        self,
        dsn: str,
        *,
        sink: AuditSink,
        queue: PostgresHandoffQueue,
        calendar: DomainCalendar,
        clock: Clock,
    ) -> None:
        """Keep the collaborators.

        ``sink`` receives every audit record; ``queue`` re-reads a ticket after a claim change;
        ``calendar`` supplies the domain date stamped on each audit record and ``clock`` the
        instant of the write.
        """
        self._dsn = dsn
        self._sink = sink
        self._queue = queue
        self._calendar = calendar
        self._clock = clock

    def _ticket_identity(self, cur: psycopg.Cursor, ticket_ref: str) -> _TicketIdentity | None:
        """The ticket's identity columns read on ``cur``, or ``None`` when no row exists."""
        cur.execute(
            "SELECT customer_id, trace_id, existing_case_number FROM handoff_outbox "
            "WHERE ticket_ref = %s",
            (ticket_ref,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        customer_id, trace_id, existing_case_number = row
        return _TicketIdentity(
            customer_id=customer_id, trace_id=trace_id, existing_case_number=existing_case_number
        )

    def _audit(
        self,
        *,
        agent_id: str,
        session_id: str,
        identity: _TicketIdentity,
        action: AuditAction,
        result: object,
    ) -> None:
        """Record one audit entry for ``action``, hashing ``result`` as what the agent changed.

        The sink opens, writes and commits on its own connection, so this runs before the
        caller's mutation commits; a sink failure propagates and aborts that mutation.
        """
        self._sink.record(
            AuditRecord(
                trace_id=identity.trace_id,
                customer_id=identity.customer_id,
                session_id=session_id,
                action=action,
                tool_result_hash=_hash(result),
                occurred_at=self._clock(),
                domain_date=self._calendar.reference_date,
                agent_id=agent_id,
            )
        )

    def _set_claim(
        self,
        *,
        agent_id: str,
        session_id: str,
        ticket_ref: str,
        claimed_by: str | None,
        action: AuditAction,
    ) -> QueueItem | None:
        """Set (or clear, when ``claimed_by`` is ``None``) the ticket's claim and audit it.

        Returns the ticket re-read from the queue after the update, or ``None`` when no
        ``handoff_outbox`` row exists for ``ticket_ref``.
        """
        claimed_at = None if claimed_by is None else self._clock()
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            identity = self._ticket_identity(cur, ticket_ref)
            if identity is None:
                return None
            cur.execute(
                "UPDATE handoff_outbox SET claimed_by = %s, claimed_at = %s WHERE ticket_ref = %s",
                (claimed_by, claimed_at, ticket_ref),
            )
            self._audit(
                agent_id=agent_id,
                session_id=session_id,
                identity=identity,
                action=action,
                result={"ticket_ref": ticket_ref, "claimed_by": claimed_by},
            )
        item = self._queue.get_ticket(ticket_ref, calendar=self._calendar)
        assert item is not None  # noqa: S101 - the update above already confirmed the row exists
        return item

    def claim_ticket(self, *, agent_id: str, session_id: str, ticket_ref: str) -> QueueItem | None:
        """Claim ``ticket_ref`` for ``agent_id``, overwriting any prior claim, and audit it.

        Returns the updated queue item, or ``None`` if no ``handoff_outbox`` row exists for
        ``ticket_ref``.
        """
        return self._set_claim(
            agent_id=agent_id,
            session_id=session_id,
            ticket_ref=ticket_ref,
            claimed_by=agent_id,
            action=AuditAction.TICKET_CLAIMED,
        )

    def release_ticket(
        self, *, agent_id: str, session_id: str, ticket_ref: str
    ) -> QueueItem | None:
        """Release ``ticket_ref``'s claim, whoever held it, and audit it.

        Returns the updated queue item, or ``None`` if no ``handoff_outbox`` row exists for
        ``ticket_ref``.
        """
        return self._set_claim(
            agent_id=agent_id,
            session_id=session_id,
            ticket_ref=ticket_ref,
            claimed_by=None,
            action=AuditAction.TICKET_RELEASED,
        )

    def _read_note(self, ticket_ref: str, ord_: int) -> Note:
        """Read a just-written note back, verified against the store rather than echoed from the
        call that wrote it."""
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT agent_id, note_text, created_at_utc FROM handoff_notes "
                "WHERE ticket_ref = %s AND ord = %s",
                (ticket_ref, ord_),
            )
            row = cur.fetchone()
        assert row is not None  # noqa: S101 - the insert above already confirmed the row exists
        agent_id, note_text, created_at = row
        return Note(agent_id=agent_id, note_text=note_text, created_at=created_at)

    def add_note(
        self, *, agent_id: str, session_id: str, ticket_ref: str, note_text: str
    ) -> Note | None:
        """Append a note to ``ticket_ref``, authored by ``agent_id``, and audit it.

        The note takes the next free ordinal for the ticket. The audit record is written before
        the insert commits; the returned note is then read back from the store.

        Returns ``None`` if no ``handoff_outbox`` row exists for ``ticket_ref``.
        """
        created_at = self._clock()
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            identity = self._ticket_identity(cur, ticket_ref)
            if identity is None:
                return None
            cur.execute(
                "SELECT COALESCE(MAX(ord), -1) + 1 FROM handoff_notes WHERE ticket_ref = %s",
                (ticket_ref,),
            )
            (ord_,) = cur.fetchone()  # type: ignore[misc]
            cur.execute(
                "INSERT INTO handoff_notes (ticket_ref, ord, agent_id, note_text, "
                "created_at_utc) VALUES (%s, %s, %s, %s, %s)",
                (ticket_ref, ord_, agent_id, note_text, created_at),
            )
            self._audit(
                agent_id=agent_id,
                session_id=session_id,
                identity=identity,
                action=AuditAction.TICKET_NOTE_ADDED,
                result={"ticket_ref": ticket_ref, "note_text": note_text},
            )
        return self._read_note(ticket_ref, ord_)

    def _read_case_status(self, case_number: str) -> CaseStatusResult:
        """Read a just-written case status back, verified against the store rather than echoed
        from the call that wrote it."""
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute("SELECT status FROM cases WHERE case_number = %s", (case_number,))
            row = cur.fetchone()
        assert row is not None  # noqa: S101 - the update above already confirmed the row exists
        return CaseStatusResult(case_number=case_number, status=CaseStatus(row[0]))

    def set_case_status(
        self, *, agent_id: str, session_id: str, ticket_ref: str, status: CaseStatus
    ) -> CaseStatusResult | None:
        """Set ``ticket_ref``'s filed case to ``status``. Updates the case row; never creates one.

        The case is found through the ticket's ``existing_case_number``. The audit record is
        written before the update commits; the stored status is then read back and returned.

        Returns ``None`` if no ``handoff_outbox`` row exists for ``ticket_ref``, or it names no
        filed case (or the named case row does not exist).

        Raises
        ------
        CaseStatusTerminal
            The case is already Resolved or Rejected.
        """
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            identity = self._ticket_identity(cur, ticket_ref)
            if identity is None or identity.existing_case_number is None:
                return None
            case_number = identity.existing_case_number
            cur.execute("SELECT status FROM cases WHERE case_number = %s", (case_number,))
            row = cur.fetchone()
            if row is None:
                return None
            current_status = CaseStatus(row[0])
            if current_status in _TERMINAL_STATUSES:
                raise CaseStatusTerminal(case_number)
            cur.execute(
                "UPDATE cases SET status = %s WHERE case_number = %s",
                (status.value, case_number),
            )
            self._audit(
                agent_id=agent_id,
                session_id=session_id,
                identity=identity,
                action=AuditAction.CASE_STATUS_SET,
                result={"case_number": case_number, "status": status.value},
            )
        return self._read_case_status(case_number)
