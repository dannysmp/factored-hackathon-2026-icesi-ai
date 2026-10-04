"""
Postgres Ticket Detail
======================

Overview
--------
One ticket's whole packet and timeline (``contracts.service_v1.console.TicketDetail``) for the
human-agent console's packet view (AC-E10-02, AC-E10-03). ``app.persistence.handoff_outbox`` never
stores a full ``TransactionFact`` or a source's localized title — only ``verified_transaction_ref``
and each source's ``section_id``/``corpus_version`` — by that module's own re-hydrate-elsewhere
design (mirroring the one-figure-one-source discipline the serving store already follows
elsewhere), so this module re-hydrates both live, on read: the transaction from the store's own
``transactions``/``products`` tables, a source's title from the current policy corpus. Everything
else on the packet — the reason codes, the policy version, the risk evidence, the stored
``corpus_version`` per source — is the frozen, as-of-handoff record and is never re-derived.

Scope
-----
In: reading ``handoff_outbox`` and its five child tables (including ``handoff_notes``, the narrow
agent writes' own table) by ``ticket_ref``, re-hydrating the
transaction and source titles, assembling ``TicketDetail``.
Out: the customer-scoped ``ToolPort`` (``app.persistence.reads``) — an agent reading a ticket has
no customer session to scope a lookup to, and the ticket can belong to any customer, so this module
is deliberately not a ``ToolPort`` and is never reachable from the customer-scoped dispatch path;
listing the queue (``app.persistence.handoff_queue``); the conversation's own timeline
(``app.persistence.dialogue_turn_log``); the console's own routes and the audit-of-agent-reads
write they must perform (ADR-17); writing to the outbox at all (``app.persistence.handoff_outbox``).

Design Principles
-----------------
- **No customer parameter, for the opposite reason ``ToolPort`` has none.** A tool takes no
  customer id so it cannot be pointed at someone else's data; this module takes no customer id
  because an agent's whole reason to read a ticket is that it is not their own — there is no
  session to scope it to, and the ticket's ``customer_id`` is not even part of ``QueueItem`` or
  ``HandoffPacket`` (only the masked label is). Authorization is the router's job (an
  ``AgentPrincipal`` reaches this at all), never a filter this module applies itself.
- **Live where the store is the only source of truth, frozen where the record must not drift.**
  A transaction's current status and a source's current title come from asking the store and the
  corpus again, the same way the queue view already computes ``age_days`` against the current
  domain calendar rather than freezing it at handoff time. The stored ``corpus_version`` is kept
  exactly as recorded, never overwritten by whatever the corpus reports today: it is the
  historical fact of what actually grounded the decision, and a corpus edit after the fact must
  not silently rewrite that record.
- **A stored reference that no longer resolves fails the whole read, visibly.** If a source's
  ``section_id`` no longer exists in the current corpus, this raises ``SourceUnavailable`` rather
  than dropping that one source or returning a packet that quietly has fewer sources than it was
  actually decided on — the feature's own failure clause is explicit that the console "never shows
  partial or stale packets as current."
- **The queue row and the timeline are each already someone else's job.** ``PostgresHandoffQueue``
  answers ``QueueItem`` (including for a ticket that has since left the open queue, via its own
  ``get_ticket``); ``PostgresDialogueTurnLog`` answers the timeline by ``trace_id``, itself a
  column this module reads off the outbox row, never derived. This module composes both with its
  own packet re-hydration; it does not re-implement either.
- **``HandoffPacket``'s own shapes, not the customer-facing tool contracts' same-named ones.**
  ``contracts.service_v1.envelope.TransactionFact``/``ProductLabel``/``Money`` are distinct
  pydantic models from ``contracts.service_v1.tools``' identically-named ones (no ``description``
  field, ``amount`` a bare ``Money | None`` rather than a provenance-carrying ``DisclosedAmount``)
  — the two contract modules are independently frozen and never interchangeable, even where a
  field set overlaps. This module imports only from ``contracts.service_v1.envelope``/``handoff``,
  never from ``contracts.service_v1.tools``, matching what ``HandoffPacket`` itself actually
  declares.
- **One connection per call**, matching every other module in ``app.persistence``.

Runtime Contract
----------------
``SourceUnavailable(Exception)``.
``PostgresTicketDetail(dsn, *, retriever, queue, turn_log)`` with
``get_ticket_detail(ticket_ref, *, calendar) -> TicketDetail | None``.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass  # A source row's own two stored columns

# Third-party libraries
import psycopg  # Serving-store driver

# Local modules
from app.domain.calendar import DomainCalendar
from app.domain.policy.models import DisputeCategory, ReasonCode
from app.domain.policy.models import TransactionStatus as PolicyTransactionStatus
from app.persistence.dialogue_turn_log import PostgresDialogueTurnLog
from app.persistence.handoff_queue import PostgresHandoffQueue
from app.persistence.reads import clamp_merchant  # Shared fit to TransactionFact.merchant's bound
from app.retrieval.lexical import LexicalRetriever
from contracts.service_v1.console import Note, TicketDetail
from contracts.service_v1.envelope import (  # The envelope-flavored shapes HandoffPacket expects
    Money,
    ProductLabel,
    RiskEvidence,
    Slot,
    SourceRef,
    TransactionFact,
)
from contracts.service_v1.handoff import (
    ActionRecord,
    CustomerLabel,
    Evidence,
    HandoffPacket,
    OpenQuestion,
)

_CONNECT_TIMEOUT_SECONDS = 5

_OUTBOX_COLUMNS = (
    "trace_id, reference_date, created_at_utc, language, trigger, customer_first_name, "
    "customer_masked_id, category, request_summary, verified_transaction_ref, "
    "attempted_action_action, attempted_action_result, existing_case_number, policy_version, "
    "risk_score, risk_interval_low, risk_interval_high, risk_base_rate, risk_threshold"
)


class SourceUnavailable(Exception):
    """A source stored on the packet no longer resolves against the current policy corpus."""


@dataclass(frozen=True, slots=True)
class _OutboxRow:
    """The scalar columns of one ``handoff_outbox`` row, read back for reconstruction."""

    trace_id: str
    reference_date: object
    created_at: object
    language: str
    trigger: str
    first_name: str
    masked_id: str
    category: str | None
    request_summary: str
    verified_transaction_ref: str | None
    attempted_action_action: str | None
    attempted_action_result: str | None
    existing_case_number: str | None
    policy_version: str
    risk_score: float | None
    risk_interval_low: float | None
    risk_interval_high: float | None
    risk_base_rate: float | None
    risk_threshold: float | None


class PostgresTicketDetail:
    """Reconstructs one ticket's whole ``TicketDetail`` from the outbox and live lookups."""

    def __init__(
        self,
        dsn: str,
        *,
        retriever: LexicalRetriever,
        queue: PostgresHandoffQueue,
        turn_log: PostgresDialogueTurnLog,
    ) -> None:
        self._dsn = dsn
        self._retriever = retriever
        self._queue = queue
        self._turn_log = turn_log

    def get_ticket_detail(
        self, ticket_ref: str, *, calendar: DomainCalendar
    ) -> TicketDetail | None:
        """``ticket_ref``'s whole detail, or ``None`` if no such ticket exists.

        Raises
        ------
        SourceUnavailable
            A source recorded on the packet no longer resolves against the current corpus.
        """
        item = self._queue.get_ticket(ticket_ref, calendar=calendar)
        if item is None:
            return None
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            row = self._outbox_row(cur, ticket_ref)
            actions = self._actions(cur, ticket_ref)
            open_questions = self._open_questions(cur, ticket_ref)
            reason_codes = self._reason_codes(cur, ticket_ref)
            sources = self._sources(cur, ticket_ref)
            notes = self._notes(cur, ticket_ref)
        packet = self._packet(
            ticket_ref,
            row,
            actions=actions,
            open_questions=open_questions,
            reason_codes=reason_codes,
            sources=sources,
        )
        timeline = self._turn_log.timeline_for(row.trace_id)
        return TicketDetail(item=item, packet=packet, timeline=timeline, notes=notes)

    def _outbox_row(self, cur: psycopg.Cursor, ticket_ref: str) -> _OutboxRow:
        cur.execute(
            f"SELECT {_OUTBOX_COLUMNS} FROM handoff_outbox WHERE ticket_ref = %s",  # noqa: S608
            (ticket_ref,),
        )
        row = cur.fetchone()
        assert row is not None  # noqa: S101 - the caller already confirmed the ticket exists
        return _OutboxRow(*row)

    def _actions(self, cur: psycopg.Cursor, ticket_ref: str) -> tuple[ActionRecord, ...]:
        cur.execute(
            "SELECT action, result FROM handoff_actions WHERE ticket_ref = %s ORDER BY ord",
            (ticket_ref,),
        )
        return tuple(
            ActionRecord(action=action, result=result) for action, result in cur.fetchall()
        )

    def _open_questions(self, cur: psycopg.Cursor, ticket_ref: str) -> tuple[OpenQuestion, ...]:
        cur.execute(
            "SELECT slot, attempts FROM handoff_open_questions WHERE ticket_ref = %s",
            (ticket_ref,),
        )
        return tuple(
            OpenQuestion(slot=Slot(slot), attempts=attempts) for slot, attempts in cur.fetchall()
        )

    def _reason_codes(self, cur: psycopg.Cursor, ticket_ref: str) -> tuple[ReasonCode, ...]:
        cur.execute(
            "SELECT reason_code FROM handoff_reason_codes WHERE ticket_ref = %s ORDER BY ord",
            (ticket_ref,),
        )
        return tuple(ReasonCode(code) for (code,) in cur.fetchall())

    def _notes(self, cur: psycopg.Cursor, ticket_ref: str) -> tuple[Note, ...]:
        cur.execute(
            "SELECT agent_id, note_text, created_at_utc FROM handoff_notes "
            "WHERE ticket_ref = %s ORDER BY ord",
            (ticket_ref,),
        )
        return tuple(
            Note(agent_id=agent_id, note_text=note_text, created_at=created_at)
            for agent_id, note_text, created_at in cur.fetchall()
        )

    def _sources(self, cur: psycopg.Cursor, ticket_ref: str) -> tuple[SourceRef, ...]:
        cur.execute(
            "SELECT section_id, corpus_version FROM handoff_sources "
            "WHERE ticket_ref = %s ORDER BY ord",
            (ticket_ref,),
        )
        return tuple(
            self._source_ref(section_id, stored_corpus_version)
            for section_id, stored_corpus_version in cur.fetchall()
        )

    def _source_ref(self, section_id: str, stored_corpus_version: str) -> SourceRef:
        """``section_id``'s current titles, paired with the corpus version actually on file.

        Raises
        ------
        SourceUnavailable
            ``section_id`` no longer exists in the current corpus.
        """
        try:
            live = self._retriever.source_ref_for(section_id)
        except KeyError as error:
            raise SourceUnavailable(
                f"section {section_id!r} no longer resolves against the current corpus"
            ) from error
        return SourceRef(
            section_id=section_id, titles=live.titles, corpus_version=stored_corpus_version
        )

    def _verified_facts(self, ref: str | None) -> tuple[TransactionFact, ...]:
        """The transaction ``ref`` re-hydrated as the envelope's own ``TransactionFact`` — the
        shape ``HandoffPacket`` actually declares, not ``contracts.service_v1.tools``'s
        differently-shaped one of the same name (no ``description``, ``amount`` a bare
        ``Money | None`` rather than a provenance-carrying ``DisclosedAmount``)."""
        if ref is None:
            return ()
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                """
                SELECT t.transaction_id, t.transaction_date, t.merchant_name, t.amount_usd,
                       t.transaction_status, p.product_type, p.last4
                FROM transactions AS t
                JOIN products AS p ON p.product_id = t.product_id
                WHERE t.transaction_id = %s
                """,
                (ref,),
            )
            row = cur.fetchone()
        if row is None:
            # The transaction the packet named at handoff time no longer resolves (deleted or
            # re-keyed data): the packet still shows what was actually verified back then, so it
            # reports no re-hydrated fact rather than fabricating one or failing the whole read —
            # unlike a source, this has no customer-visible presentation obligation to satisfy.
            return ()
        (transaction_id, occurred_at, merchant, amount_usd, status, product_type, last4) = row
        money = None if amount_usd is None else Money(amount=amount_usd, currency="USD")
        return (
            TransactionFact(
                ref=transaction_id,
                occurred_on=occurred_at.date(),
                merchant=clamp_merchant(merchant),
                amount=money,
                product=ProductLabel(name=product_type or "unknown", last4=last4),
                status=PolicyTransactionStatus(status),
            ),
        )

    def _packet(
        self,
        ticket_ref: str,
        row: _OutboxRow,
        *,
        actions: tuple[ActionRecord, ...],
        open_questions: tuple[OpenQuestion, ...],
        reason_codes: tuple[ReasonCode, ...],
        sources: tuple[SourceRef, ...],
    ) -> HandoffPacket:
        attempted_action = (
            ActionRecord(action=row.attempted_action_action, result=row.attempted_action_result)
            if row.attempted_action_action is not None and row.attempted_action_result is not None
            else None
        )
        risk = (
            RiskEvidence(
                score=row.risk_score,
                interval_low=row.risk_interval_low,
                interval_high=row.risk_interval_high,
                base_rate=row.risk_base_rate,
                threshold=row.risk_threshold,
            )
            if row.risk_score is not None
            else None
        )
        return HandoffPacket(
            ticket_ref=ticket_ref,
            reference_date=row.reference_date,
            created_at=row.created_at,
            language=row.language,
            needs_language_routing=row.language != "es",
            trigger=row.trigger,
            customer=CustomerLabel(first_name=row.first_name, masked_id=row.masked_id),
            category=DisputeCategory(row.category) if row.category is not None else None,
            request_summary=row.request_summary,
            verified_facts=self._verified_facts(row.verified_transaction_ref),
            actions=actions,
            attempted_action=attempted_action,
            existing_case_number=row.existing_case_number,
            evidence=Evidence(
                reason_codes=reason_codes,
                policy_version=row.policy_version,
                sources=sources,
                risk=risk,
            ),
            open_questions=open_questions,
        )
