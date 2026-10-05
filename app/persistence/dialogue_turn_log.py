"""
Postgres Dialogue Turn Log
==========================

Overview
--------
The ``DialogueTurnLog`` (``app.conversation.controller``) backed by Postgres: one row per turn the
controller actually advances, backing ``contracts/service_v1/console.py``'s ``TimelineEntry`` for
the human-agent console's audit timeline.

Scope
-----
In: ``record`` and ``timeline_for`` against the ``dialogue_turn_log`` table.
Out: deciding which turns are worth recording (``app.conversation.controller``, which never calls
this for a replayed turn), the queue and packet read side (``app.persistence.handoff_queue``).

Design Principles
-----------------
- **A repeated turn writes nothing twice.** ``UNIQUE (session_id, turn_id)`` is the table's own
  safety net; ``record`` resolves a conflict there with ``ON CONFLICT ... DO NOTHING`` rather than
  raising, since the caller never intends to write the same turn's history more than once and
  there is nothing to reconcile if it tries (unlike the dialogue-state race, this table has no
  content to disagree over).
- **This write is never allowed to change what the customer is told.** ``record`` raises exactly
  ``psycopg.Error`` on a genuine failure to reach the store; the caller (``DialogueController``)
  catches it and logs a warning, since losing one timeline entry degrades the console's own view
  of a conversation, not the conversation itself, a materially different failure mode from a lost
  handoff.
- **One connection per call**, matching every other module in ``app.persistence``.

Runtime Contract
----------------
``PostgresDialogueTurnLog(dsn)`` with ``record(entry, *, session_id) -> None`` and
``timeline_for(trace_id) -> tuple[TimelineEntry, ...]``, ordered by ``occurred_at`` and, for turns
recorded at the same instant, by the order they were written.
"""

from __future__ import annotations

# Standard libraries
from typing import Any  # Raw driver rows

# Third-party libraries
import psycopg  # Serving-store driver

# Local modules
from app.domain.policy.models import ReasonCode
from contracts.service_v1.console import TimelineEntry
from contracts.service_v1.envelope import Intent

_CONNECT_TIMEOUT_SECONDS = 5

_COLUMNS = (
    "occurred_at_utc, trace_id, intent, state_before, state_after, render_mode, reason_code, "
    "policy_version"
)

# A conversation's turns are read by trace identifier and ordered by when they occurred, then by
# the order they were written when two share an instant. The column list is a module constant,
# not request data.
_SELECT = (
    f"SELECT turn_id, {_COLUMNS} FROM dialogue_turn_log "  # noqa: S608
    "WHERE trace_id = %s ORDER BY occurred_at_utc, id"
)


def _row_to_entry(row: Any) -> TimelineEntry:
    """A ``dialogue_turn_log`` row, ``turn_id`` then ``_COLUMNS`` order, as a ``TimelineEntry``."""
    (
        turn_id,
        occurred_at,
        trace_id,
        intent,
        state_before,
        state_after,
        render_mode,
        reason_code,
        policy_version,
    ) = row
    return TimelineEntry(
        occurred_at=occurred_at,
        trace_id=trace_id,
        turn_id=turn_id,
        intent=Intent(intent),
        state_before=state_before,
        state_after=state_after,
        render_mode=render_mode,
        reason_code=ReasonCode(reason_code) if reason_code is not None else None,
        policy_version=policy_version,
    )


class PostgresDialogueTurnLog:
    """A ``DialogueTurnLog`` backed by the ``dialogue_turn_log`` table."""

    def __init__(self, dsn: str) -> None:
        """Keep the DSN; a connection is opened per call."""
        self._dsn = dsn

    def record(self, entry: TimelineEntry, *, session_id: str) -> None:
        """Write ``entry``'s row for ``(session_id, entry.turn_id)``; a repeat of the same pair is a
        no-op.

        The insert commits when the connection block exits without an error. The first write of a
        pair wins: a conflicting repeat leaves the stored row unchanged.

        Raises
        ------
        psycopg.Error
            The store could not be reached; the caller logs and continues without this entry.
        """
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                f"""
                INSERT INTO dialogue_turn_log (
                    session_id, turn_id, {_COLUMNS}
                ) VALUES (
                    %(session_id)s, %(turn_id)s, %(occurred_at)s, %(trace_id)s, %(intent)s,
                    %(state_before)s, %(state_after)s, %(render_mode)s, %(reason_code)s,
                    %(policy_version)s
                )
                ON CONFLICT (session_id, turn_id) DO NOTHING
                """,  # noqa: S608
                {
                    "session_id": session_id,
                    "turn_id": entry.turn_id,
                    "occurred_at": entry.occurred_at,
                    "trace_id": entry.trace_id,
                    "intent": entry.intent.value,
                    "state_before": entry.state_before,
                    "state_after": entry.state_after,
                    "render_mode": entry.render_mode,
                    "reason_code": entry.reason_code.value
                    if entry.reason_code is not None
                    else None,
                    "policy_version": entry.policy_version,
                },
            )

    def timeline_for(self, trace_id: str) -> tuple[TimelineEntry, ...]:
        """Every recorded turn for ``trace_id``, in the order they occurred.

        Returns an empty tuple when the trace has no recorded turns. A store failure propagates
        as ``psycopg.Error``.
        """
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(_SELECT, (trace_id,))
            return tuple(_row_to_entry(row) for row in cur.fetchall())
