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
In: ``HandoffOutbox`` (the port), ``PostgresHandoffOutbox``, ``content_fingerprint`` (the replay
comparison's own digest, over a ``HandoffContent``).
Out: ``HandoffContent`` and building the packet's *content* (both
``app.conversation.handoff``, the conversation layer this module depends on for the shape of what
it writes — imported here only to validate what is written), deciding when a handoff is warranted
(the controller), reading the outbox back (the console, a later slice).

Design Principles
-----------------
- **Idempotent by (session_id, turn_id), not by content.** A retried turn that would produce an
  identical handoff gets back the same ticket, never a second row — the same doctrine
  ``app.persistence.reads`` already uses for case creation: a proactive lookup for the ordinary
  sequential replay, the table's ``UNIQUE (session_id, turn_id)`` constraint for the true race. The
  race path distinguishes that constraint from the ``ticket_ref`` primary key by name
  (``exc.diag.constraint_name``, the same idiom ``reads.py`` uses): only the former is an ordinary
  replay to reclassify, the latter is a genuine, loggable failure.
- **A replay never trusts the caller's fresh content over what is on file.** A disagreement raises
  ``HandoffReplayMismatch`` rather than silently returning a packet built from the new call's
  content, which could permanently disagree with the row a later reader (the console) reads
  directly. The comparison is a single stored digest (``content_fingerprint``, migration 0009) over
  every field ``HandoffContent`` carries except identity and timing fields (see
  ``content_fingerprint``'s own docstring for exactly which, and why) — not a hand-enumerated list
  of columns and child tables kept in sync by hand as ``HandoffContent`` grows, which had already
  drifted out of sync once. Computing the digest from ``content`` itself, not from what happens to
  be persisted in a column, also means it covers a field's full value even where only part of it is
  stored (a full ``TransactionFact``, not just the ``verified_transaction_ref`` column that survives
  it) — a stronger comparison than the one it replaces, not just a shorter one.
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
``HandoffReplayMismatch``, ``PostgresHandoffOutbox(dsn)``, ``content_fingerprint(content) -> str``.
"""

from __future__ import annotations

# Standard libraries
import hashlib  # The stored content fingerprint
import json  # Canonical serialization the fingerprint is computed over
import logging  # Progress events, never print
import secrets  # Unguessable suffix of a generated ticket reference
from dataclasses import fields  # Every HandoffContent field, without naming them by hand
from datetime import date  # The reference date the packet used

# Third-party libraries
import psycopg  # Serving-store driver
import psycopg.errors  # Distinguishing a unique-constraint race from any other store failure
from pydantic import BaseModel  # Every nested contract value HandoffContent can hold

# Local modules
from app.conversation.handoff import HandoffContent, build_packet, mask_customer_id
from app.security.middleware import current_request_id  # Correlates a log line to its request
from contracts.service_v1.handoff import HandoffPacket  # The packet this module writes

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 5
_SESSION_TURN_CONSTRAINT = "handoff_outbox_session_turn_unique"

# Fields identity or timing describes, not the handoff itself: excluded from the fingerprint the
# same way _existing_row's predecessor excluded the matching columns. reference_date and created_at
# are expected to vary slightly between an original call and a genuine retry's freshly-read clock,
# which a byte-for-byte comparison would wrongly flag; customer_id's masked label stands in for the
# raw identifier, the same substitution the stored row itself makes.
_FINGERPRINT_EXCLUDED_FIELDS = frozenset({"reference_date", "created_at", "customer_id"})


class HandoffReplayMismatch(Exception):
    """A repeated (session_id, turn_id) was given content that disagrees with what is on file."""


def _fingerprint_default(value: object) -> object:
    """A JSON-safe form for a value ``json.dumps`` cannot serialize on its own.

    Covers every shape a fingerprinted field holds today that isn't already natively
    JSON-serializable — a nested contract model, directly or inside a tuple — generically, by type
    rather than by field name, so a field added later needs a new case here only if it introduces a
    genuinely new kind of value. Anything else raises rather than silently degrading to a string
    representation that could hide a real difference between two otherwise-distinct values.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"cannot fingerprint a {type(value).__name__} value")


def content_fingerprint(content: HandoffContent) -> str:
    """A stable digest over every field ``content`` carries, except identity and timing fields.

    Iterates ``HandoffContent``'s own fields (``dataclasses.fields``) rather than naming each one,
    so a field added to ``HandoffContent`` later is covered automatically here with no matching
    edit — closing the obligation the hand-enumerated column list this replaces used to carry, and
    already drifted out of sync once. ``json.dumps(..., sort_keys=True)`` gives one canonical
    ordering regardless of ``HandoffContent``'s own field declaration order, so the digest is
    stable even if that order ever changes.

    ``open_questions`` is sorted by slot before hashing: unlike every other repeating part of a
    handoff, ``handoff_open_questions`` has no ``ord`` column and is keyed ``(ticket_ref, slot)``
    (migration 0006) — the table itself has no order to preserve, so two calls differing only in
    the order they listed the same open questions must fingerprint identically, matching what the
    comparison this replaces already did with a ``frozenset`` for this one field.
    """
    payload = {
        f.name: getattr(content, f.name)
        for f in fields(content)
        if f.name not in _FINGERPRINT_EXCLUDED_FIELDS
    }
    payload["customer_id"] = mask_customer_id(content.customer_id)
    if "open_questions" in payload:
        payload["open_questions"] = sorted(payload["open_questions"], key=lambda q: q.slot.value)
    canonical = json.dumps(payload, sort_keys=True, default=_fingerprint_default)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    ) -> tuple[str, str | None] | None:
        """The existing row's ticket and stored content fingerprint, or ``None``.

        The fingerprint itself is ``None`` for a row written before migration 0009 added the
        column: nothing here backfills one, since it would cover fields this table never persisted
        in the first place (see ``content_fingerprint``'s own docstring).
        """
        cur.execute(
            "SELECT ticket_ref, content_fingerprint FROM handoff_outbox "
            "WHERE session_id = %s AND turn_id = %s",
            (session_id, turn_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return str(row[0]), (str(row[1]) if row[1] is not None else None)

    def _replay_or_mismatch(
        self,
        content: HandoffContent,
        *,
        session_id: str,
        turn_id: str,
        trace_id: str,
        ticket_ref: str,
        stored_fingerprint: str | None,
    ) -> HandoffPacket:
        """The replay's packet, or ``HandoffReplayMismatch`` if ``content`` disagrees with it.

        Never trusts ``content`` over what is on file: a repeated id pair whose stored fingerprint
        differs from this call's own is refused rather than silently answered with the new call's
        version, which could permanently disagree with the row a later reader reads directly. A
        ``None`` stored fingerprint (a row that predates migration 0009) is unverifiable, never
        trusted as an ordinary replay either — it raises the same way, with its own message.
        """
        if stored_fingerprint is None:
            raise HandoffReplayMismatch(
                f"session {session_id} turn {turn_id} already recorded ticket {ticket_ref} "
                "with no stored fingerprint to verify against"
            )
        if stored_fingerprint != content_fingerprint(content):
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
                    ticket_ref, stored_fingerprint = existing
                    return self._replay_or_mismatch(
                        content,
                        session_id=session_id,
                        turn_id=turn_id,
                        trace_id=trace_id,
                        ticket_ref=ticket_ref,
                        stored_fingerprint=stored_fingerprint,
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
                    fingerprint=content_fingerprint(content),
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
            ticket_ref, stored_fingerprint = raced
            return self._replay_or_mismatch(
                content,
                session_id=session_id,
                turn_id=turn_id,
                trace_id=trace_id,
                ticket_ref=ticket_ref,
                stored_fingerprint=stored_fingerprint,
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
        fingerprint: str,
    ) -> None:
        risk = packet.evidence.risk
        cur.execute(
            """
            INSERT INTO handoff_outbox (
                ticket_ref, customer_id, session_id, trace_id, turn_id, reference_date,
                created_at_utc, language, trigger, customer_first_name, customer_masked_id,
                category, request_summary, verified_transaction_ref, attempted_action_action,
                attempted_action_result, existing_case_number, policy_version, risk_score,
                risk_interval_low, risk_interval_high, risk_base_rate, content_fingerprint
            ) VALUES (
                %(ticket_ref)s, %(customer_id)s, %(session_id)s, %(trace_id)s, %(turn_id)s,
                %(reference_date)s, %(created_at)s, %(language)s, %(trigger)s, %(first_name)s,
                %(masked_id)s, %(category)s, %(request_summary)s, %(verified_transaction_ref)s,
                %(attempted_action)s, %(attempted_result)s, %(existing_case_number)s,
                %(policy_version)s, %(risk_score)s, %(risk_low)s, %(risk_high)s, %(risk_base)s,
                %(fingerprint)s
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
                "fingerprint": fingerprint,
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
