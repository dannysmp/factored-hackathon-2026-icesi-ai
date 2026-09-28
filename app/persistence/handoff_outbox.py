"""
Postgres Handoff Outbox
=======================

Overview
--------
Writes the handoff builder's packet (``app.conversation.handoff.build_packet``) to a local outbox
row (migration 0006), so the human path never depends on the model, retrieval or any other tool
being available. Generates the ticket reference itself, the same place ``app.persistence.reads``
generates a case number — right before the insert that makes it real, with a proactive check for
the ordinary replay and the table's own unique constraint as the true race's safety net.

Scope
-----
In: ``HandoffContent`` (everything a packet needs but its ticket), ``HandoffOutbox`` (the port),
``PostgresHandoffOutbox``.
Out: building the packet's *content* (``app.conversation.handoff.build_packet``, called from
here only to validate what is written), deciding when a handoff is warranted (the controller),
reading the outbox back (the console, a later slice).

Design Principles
-----------------
- **Idempotent by (session_id, turn_id), not by content.** A retried turn that would produce an
  identical handoff gets back the same ticket, never a second row — the same doctrine
  ``app.persistence.reads`` already uses for case creation: a proactive lookup for the ordinary
  sequential replay, the table's ``UNIQUE (session_id, turn_id)`` constraint for the true race. The
  race path distinguishes that constraint from the ``ticket_ref`` primary key by name
  (``exc.diag.constraint_name``, the same idiom ``reads.py`` uses): only the former is an ordinary
  replay to reclassify, the latter is a genuine, loggable failure.
- **A replay never trusts the caller's fresh content over what is on file.** The persisted row's
  own comparable fields (trigger, request summary, policy version, category, customer label,
  language) are read back and checked against ``content`` before a replay is treated as ordinary;
  a disagreement raises ``HandoffReplayMismatch`` rather than silently returning a packet built
  from the new call's content, which could permanently disagree with the row a later reader (the
  console) reads directly. ``verified_facts`` and ``sources`` are not part of this comparison,
  since neither is fully reconstructable from the outbox row alone (their titles/full facts are
  re-hydrated at read time elsewhere, by design) — an identical retry of the same call produces
  the same values for these too, so checking the fields the row does store is what actually
  detects the failure mode this guards against: a caller passing genuinely different content for
  an id pair it has already used.
- **One raw identifier, one masked label, one source.** ``content.customer_id`` is the only
  customer identifier ``record`` reads: the row's own identity column and the packet's masked
  label are both derived from it, so the two can never name different customers the way two
  independent parameters could.
- **Every log line carries a trace and a request identifier**, matching this project's logging
  standard: ``trace_id`` is the caller's conversation-level identifier (a parameter, since this
  store is not constructed per conversation the way ``PostgresToolPort`` is), ``request_id`` is
  the current HTTP request's (``current_request_id()``) — the two answer different questions and
  are never conflated into one column or one log field.
- **One connection per call**, matching every other module in ``app.persistence``.
- **No jsonb.** Every column is typed, matching migration 0006's own tables; the four child tables
  hold the packet's bounded repeating groups (actions, open questions, reason codes, sources).
- **Nothing here decides the packet is warranted or what it says** — ``HandoffContent`` is already
  a fully-formed set of facts by the time it reaches this module; the controller (a later change)
  is the one that decides a handoff is happening at all.

Runtime Contract
----------------
``HandoffContent`` (dataclass), ``HandoffReplayMismatch``, ``PostgresHandoffOutbox(dsn)``.
"""

from __future__ import annotations

# Standard libraries
import logging  # Progress events, never print
import secrets  # Unguessable suffix of a generated ticket reference
from dataclasses import dataclass  # Everything a packet needs but its ticket
from datetime import date  # The reference date the packet used

# Third-party libraries
import psycopg  # Serving-store driver
import psycopg.errors  # Distinguishing a unique-constraint race from any other store failure

# Local modules
from app.conversation.handoff import build_packet, mask_customer_id
from app.domain.policy.models import DisputeCategory, ReasonCode  # Shared vocabulary
from app.security.middleware import current_request_id  # Correlates a log line to its request
from contracts.service_v1.envelope import (  # Shared base, types and vocabulary
    Lang,
    RiskEvidence,
    SourceRef,
    TransactionFact,
    UtcDatetime,
)
from contracts.service_v1.handoff import (  # The packet this module writes
    ActionRecord,
    HandoffPacket,
    HandoffTrigger,
    OpenQuestion,
)

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 5
_SESSION_TURN_CONSTRAINT = "handoff_outbox_session_turn_unique"


class HandoffReplayMismatch(Exception):
    """A repeated (session_id, turn_id) was given content that disagrees with what is on file."""


@dataclass(frozen=True, slots=True)
class HandoffContent:
    """Everything ``build_packet`` needs except the ticket reference, which this module mints."""

    reference_date: date
    created_at: UtcDatetime
    language: Lang
    trigger: HandoffTrigger
    first_name: str
    customer_id: str
    request_summary: str
    reason_codes: tuple[ReasonCode, ...]
    policy_version: str
    category: DisputeCategory | None = None
    verified_facts: tuple[TransactionFact, ...] = ()
    actions: tuple[ActionRecord, ...] = ()
    attempted_action: ActionRecord | None = None
    existing_case_number: str | None = None
    sources: tuple[SourceRef, ...] = ()
    risk: RiskEvidence | None = None
    open_questions: tuple[OpenQuestion, ...] = ()


def _new_ticket_ref(reference_date: date) -> str:
    """A short, readable ticket reference: what a customer quotes on the phone."""
    return f"T-{reference_date:%Y%m%d}-{secrets.token_hex(4).upper()}"


class PostgresHandoffOutbox:
    """The outbox backed by the ``handoff_outbox`` table and its four children."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _log_failure(self, event: str, session_id: str, trace_id: str) -> None:
        logger.warning(
            "%s session_id=%s trace_id=%s request_id=%s",
            event,
            session_id,
            trace_id,
            current_request_id(),
        )

    def _log_replay(self, session_id: str, turn_id: str, ticket_ref: str, trace_id: str) -> None:
        logger.info(
            "handoff_replayed session_id=%s turn_id=%s ticket_ref=%s trace_id=%s request_id=%s",
            session_id,
            turn_id,
            ticket_ref,
            trace_id,
            current_request_id(),
        )

    def _existing_row(
        self, cur: psycopg.Cursor, session_id: str, turn_id: str
    ) -> tuple[str, tuple[object, ...]] | None:
        """The existing row's ticket and its comparable fields, or ``None`` for no match."""
        cur.execute(
            "SELECT ticket_ref, trigger, request_summary, policy_version, category, "
            "customer_first_name, customer_masked_id, language "
            "FROM handoff_outbox WHERE session_id = %s AND turn_id = %s",
            (session_id, turn_id),
        )
        row = cur.fetchone()
        return None if row is None else (str(row[0]), tuple(row[1:]))

    def _comparable_fields(self, content: HandoffContent) -> tuple[object, ...]:
        """``content``'s own values, in the same order ``_existing_row`` reads them back."""
        return (
            content.trigger.value,
            content.request_summary,
            content.policy_version,
            content.category.value if content.category is not None else None,
            content.first_name,
            mask_customer_id(content.customer_id),
            content.language,
        )

    def _replay_or_mismatch(
        self,
        content: HandoffContent,
        *,
        session_id: str,
        turn_id: str,
        trace_id: str,
        ticket_ref: str,
        stored: tuple[object, ...],
    ) -> HandoffPacket:
        """The replay's packet, or ``HandoffReplayMismatch`` if ``content`` disagrees with it.

        Never trusts ``content`` over what is on file: a repeated id pair whose stored fields
        differ from this call's own is refused rather than silently answered with the new call's
        version, which could permanently disagree with the row a later reader reads directly.
        """
        if stored != self._comparable_fields(content):
            raise HandoffReplayMismatch(
                f"session {session_id} turn {turn_id} already recorded ticket {ticket_ref} "
                "with different content"
            )
        self._log_replay(session_id, turn_id, ticket_ref, trace_id)
        return self._packet_for(content, ticket_ref=ticket_ref)

    def record(
        self, content: HandoffContent, *, session_id: str, turn_id: str, trace_id: str
    ) -> HandoffPacket:
        """Write the outbox row for ``content`` and return its packet.

        A repeated ``(session_id, turn_id)`` returns the same packet the first call produced,
        never a second row.

        Raises
        ------
        HandoffReplayMismatch
            ``(session_id, turn_id)`` was already recorded with content that disagrees with this
            call's.
        """
        try:
            with (
                psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
                conn.cursor() as cur,
            ):
                existing = self._existing_row(cur, session_id, turn_id)
                if existing is not None:
                    ticket_ref, stored = existing
                    return self._replay_or_mismatch(
                        content,
                        session_id=session_id,
                        turn_id=turn_id,
                        trace_id=trace_id,
                        ticket_ref=ticket_ref,
                        stored=stored,
                    )

                ticket_ref = _new_ticket_ref(content.reference_date)
                packet = self._packet_for(content, ticket_ref=ticket_ref)
                self._insert(
                    cur,
                    packet,
                    session_id=session_id,
                    customer_id=content.customer_id,
                    trace_id=trace_id,
                    turn_id=turn_id,
                )
                return packet
        except psycopg.errors.UniqueViolation as exc:
            if exc.diag.constraint_name != _SESSION_TURN_CONSTRAINT:
                # Not the (session_id, turn_id) race this method is idempotent against — most
                # plausibly a ticket_ref collision (astronomically unlikely, never silently
                # reclassified as an ordinary replay) or a constraint this module does not know
                # about. Either way, a genuine, loggable failure, not a replay.
                self._log_failure("handoff_outbox_write_failed", session_id, trace_id)
                raise
            with (
                psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
                conn.cursor() as cur,
            ):
                raced = self._existing_row(cur, session_id, turn_id)
            if raced is None:  # pragma: no cover
                # Unreachable in practice: the violation just raised on this exact constraint
                # guarantees a matching (session_id, turn_id) row exists.
                self._log_failure("handoff_outbox_write_failed", session_id, trace_id)
                raise
            ticket_ref, stored = raced
            return self._replay_or_mismatch(
                content,
                session_id=session_id,
                turn_id=turn_id,
                trace_id=trace_id,
                ticket_ref=ticket_ref,
                stored=stored,
            )
        except psycopg.Error:
            self._log_failure("handoff_outbox_write_failed", session_id, trace_id)
            raise

    def _packet_for(self, content: HandoffContent, *, ticket_ref: str) -> HandoffPacket:
        return build_packet(
            ticket_ref=ticket_ref,
            reference_date=content.reference_date,
            created_at=content.created_at,
            language=content.language,
            trigger=content.trigger,
            first_name=content.first_name,
            customer_id=content.customer_id,
            request_summary=content.request_summary,
            reason_codes=content.reason_codes,
            policy_version=content.policy_version,
            category=content.category,
            verified_facts=content.verified_facts,
            actions=content.actions,
            attempted_action=content.attempted_action,
            existing_case_number=content.existing_case_number,
            sources=content.sources,
            risk=content.risk,
            open_questions=content.open_questions,
        )

    def _insert(
        self,
        cur: psycopg.Cursor,
        packet: HandoffPacket,
        *,
        session_id: str,
        customer_id: str,
        trace_id: str,
        turn_id: str,
    ) -> None:
        risk = packet.evidence.risk
        cur.execute(
            """
            INSERT INTO handoff_outbox (
                ticket_ref, customer_id, session_id, trace_id, turn_id, reference_date,
                created_at_utc, language, trigger, customer_first_name, customer_masked_id,
                category, request_summary, verified_transaction_ref, attempted_action_action,
                attempted_action_result, existing_case_number, policy_version, risk_score,
                risk_interval_low, risk_interval_high, risk_base_rate
            ) VALUES (
                %(ticket_ref)s, %(customer_id)s, %(session_id)s, %(trace_id)s, %(turn_id)s,
                %(reference_date)s, %(created_at)s, %(language)s, %(trigger)s, %(first_name)s,
                %(masked_id)s, %(category)s, %(request_summary)s, %(verified_transaction_ref)s,
                %(attempted_action)s, %(attempted_result)s, %(existing_case_number)s,
                %(policy_version)s, %(risk_score)s, %(risk_low)s, %(risk_high)s, %(risk_base)s
            )
            """,
            {
                "ticket_ref": packet.ticket_ref,
                "customer_id": customer_id,
                "session_id": session_id,
                "trace_id": trace_id,
                "turn_id": turn_id,
                "reference_date": packet.reference_date,
                "created_at": packet.created_at,
                "language": packet.language,
                "trigger": packet.trigger.value,
                "first_name": packet.customer.first_name,
                "masked_id": packet.customer.masked_id,
                "category": packet.category.value if packet.category is not None else None,
                "request_summary": packet.request_summary,
                "verified_transaction_ref": packet.verified_facts[0].ref
                if packet.verified_facts
                else None,
                "attempted_action": packet.attempted_action.action
                if packet.attempted_action is not None
                else None,
                "attempted_result": packet.attempted_action.result
                if packet.attempted_action is not None
                else None,
                "existing_case_number": packet.existing_case_number,
                "policy_version": packet.evidence.policy_version,
                "risk_score": risk.score if risk is not None else None,
                "risk_low": risk.interval_low if risk is not None else None,
                "risk_high": risk.interval_high if risk is not None else None,
                "risk_base": risk.base_rate if risk is not None else None,
            },
        )
        for ordinal, action in enumerate(packet.actions):
            cur.execute(
                "INSERT INTO handoff_actions (ticket_ref, ord, action, result) "
                "VALUES (%s, %s, %s, %s)",
                (packet.ticket_ref, ordinal, action.action, action.result),
            )
        for question in packet.open_questions:
            cur.execute(
                "INSERT INTO handoff_open_questions (ticket_ref, slot, attempts) "
                "VALUES (%s, %s, %s)",
                (packet.ticket_ref, question.slot.value, question.attempts),
            )
        for ordinal, code in enumerate(packet.evidence.reason_codes):
            cur.execute(
                "INSERT INTO handoff_reason_codes (ticket_ref, ord, reason_code) "
                "VALUES (%s, %s, %s)",
                (packet.ticket_ref, ordinal, code.value),
            )
        for ordinal, source in enumerate(packet.evidence.sources):
            cur.execute(
                "INSERT INTO handoff_sources (ticket_ref, ord, section_id, corpus_version) "
                "VALUES (%s, %s, %s, %s)",
                (packet.ticket_ref, ordinal, source.section_id, source.corpus_version),
            )
