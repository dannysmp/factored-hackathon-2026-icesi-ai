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
  sequential replay, the table's ``UNIQUE (session_id, turn_id)`` constraint for the true race.
- **One connection per call**, matching every other module in ``app.persistence``.
- **No jsonb.** Every column is typed, matching migration 0006's own tables; the four child tables
  hold the packet's bounded repeating groups (actions, open questions, reason codes, sources).
- **Nothing here decides the packet is warranted or what it says** — ``HandoffContent`` is already
  a fully-formed set of facts by the time it reaches this module; the controller (a later change)
  is the one that decides a handoff is happening at all.

Runtime Contract
----------------
``HandoffContent`` (dataclass), ``HandoffOutbox`` (protocol), ``PostgresHandoffOutbox(dsn)``.
"""

from __future__ import annotations

# Standard libraries
import logging  # Progress events, never print
import secrets  # Unguessable suffix of a generated ticket reference
from dataclasses import dataclass, field  # Everything a packet needs but its ticket
from datetime import date  # The reference date the packet used

# Third-party libraries
import psycopg  # Serving-store driver
import psycopg.errors  # Distinguishing a unique-constraint race from any other store failure

# Local modules
from app.conversation.handoff import build_packet
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
    open_questions: tuple[OpenQuestion, ...] = field(default_factory=tuple)


def _new_ticket_ref(reference_date: date) -> str:
    """A short, readable ticket reference: what a customer quotes on the phone."""
    return f"T-{reference_date:%Y%m%d}-{secrets.token_hex(4).upper()}"


class PostgresHandoffOutbox:
    """The ``HandoffOutbox`` backed by the ``handoff_outbox`` table and its four children."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _log_failure(self, event: str, session_id: str) -> None:
        logger.warning("%s session_id=%s request_id=%s", event, session_id, current_request_id())

    def _log_replay(self, session_id: str, turn_id: str, ticket_ref: str) -> None:
        logger.info(
            "handoff_replayed session_id=%s turn_id=%s ticket_ref=%s request_id=%s",
            session_id,
            turn_id,
            ticket_ref,
            current_request_id(),
        )

    def _existing_ticket(self, cur: psycopg.Cursor, session_id: str, turn_id: str) -> str | None:
        cur.execute(
            "SELECT ticket_ref FROM handoff_outbox WHERE session_id = %s AND turn_id = %s",
            (session_id, turn_id),
        )
        row = cur.fetchone()
        return None if row is None else str(row[0])

    def record(
        self, content: HandoffContent, *, session_id: str, customer_id: str, turn_id: str
    ) -> HandoffPacket:
        """Write the outbox row for ``content`` and return its packet.

        A repeated ``(session_id, turn_id)`` returns the same packet the first call produced,
        never a second row: ``customer_id`` is duplicated in the signature deliberately, since
        this module needs it for the row's own identity column, while ``content.customer_id`` is
        the value the packet's masked label is built from — the same value in every real call,
        kept as two parameters because they answer different questions (who filed the row, what
        the packet says).
        """
        try:
            with (
                psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
                conn.cursor() as cur,
            ):
                existing = self._existing_ticket(cur, session_id, turn_id)
                if existing is not None:
                    self._log_replay(session_id, turn_id, existing)
                    return self._packet_for(content, ticket_ref=existing)

                ticket_ref = _new_ticket_ref(content.reference_date)
                packet = self._packet_for(content, ticket_ref=ticket_ref)
                self._insert(
                    cur, packet, session_id=session_id, customer_id=customer_id, turn_id=turn_id
                )
                return packet
        except psycopg.errors.UniqueViolation:
            with (
                psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
                conn.cursor() as cur,
            ):
                raced = self._existing_ticket(cur, session_id, turn_id)
            if raced is None:  # pragma: no cover
                # Unreachable in practice: the violation just raised guarantees a matching row.
                raise
            self._log_replay(session_id, turn_id, raced)
            return self._packet_for(content, ticket_ref=raced)
        except psycopg.Error:
            self._log_failure("handoff_outbox_write_failed", session_id)
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
                "trace_id": current_request_id(),
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
