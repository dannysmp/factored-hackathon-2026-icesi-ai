"""
Postgres Dialogue Store
=======================

Overview
--------
The ``DialogueStore`` (``app.conversation.store``) backed by Postgres, replacing
``InMemoryDialogueStore`` once a deployment has a database. Reproduces that reference
implementation's behavior bit for bit, including that a session's very first ``save`` always
succeeds whatever ``expected_version`` the caller passes, rather than defining its own semantics.

Scope
-----
In: ``get`` and ``save`` against the ``dialogue_state`` table.
Out: the state model and its transitions (``app.conversation.state``), the store's own port
(``app.conversation.store``), the handoff outbox (a separate table and module: a handoff is
recorded independently of where a conversation's state currently stands).

Design Principles
-----------------
- **Proactive check for the ordinary sequential replay, the unique constraint for the true race**
  (the same approach ``app.persistence.reads`` uses for case-creation idempotency): a plain
  ``SELECT`` classifies a repeated turn id or a stale version cheaply, in the common case; the
  ``INSERT ... ON CONFLICT ... WHERE version = expected_version`` statement is the single
  database-enforced source of truth a genuine race resolves against. When its ``RETURNING`` comes
  back empty, a fresh read is reclassified the same way, and this second classification can only
  ever raise: the row now definitely exists (a conflict-free insert never reaches ``ON CONFLICT``),
  and its version can never again equal the failed call's ``expected_version``, since a version
  only ever increases.
- **A fresh insert always succeeds.** ``ON CONFLICT``'s ``WHERE`` clause only ever gates the
  *update* branch; a session with no existing row is a plain insert, unconditional on
  ``expected_version``, the same behavior ``InMemoryDialogueStore.save`` has (it does not
  special-case ``current is None`` either). The caller disciplines itself to pass a consistent
  ``expected_version`` for a fresh session; the store does not need to.
- **One connection per call**, matching every other module in ``app.persistence`` (there is no
  pooling).

Runtime Contract
----------------
``PostgresDialogueStore(dsn)`` implementing ``app.conversation.store.DialogueStore``:
``get(session_id) -> DialogueState | None`` and
``save(state, *, expected_version, turn_id, now) -> DialogueState``. ``save`` raises
``DuplicateTurn`` (carrying the state that turn produced) for a repeated turn id and ``Conflict``
for a stale ``expected_version``; a driver failure is logged and re-raised as ``psycopg.Error``.
"""

from __future__ import annotations

# Standard libraries
import logging  # Progress events, never print
from datetime import datetime  # The instant a save is recorded at
from typing import Any, NoReturn  # Raw driver rows; the post-race reclassification never returns

# Third-party libraries
import psycopg  # Serving-store driver
from psycopg import Cursor  # Type of the cursor helpers share within one connection

# Local modules
from app.conversation.state import ConversationPhase, DialogueState
from app.conversation.store import Conflict, DuplicateTurn
from app.domain.policy.models import DisputeCategory  # The category a dispute falls under
from app.security.middleware import current_request_id  # Correlates a log line to its request
from contracts.service_v1.envelope import Slot  # The element being asked for

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 5

_COLUMNS = (
    "version, lang, phase, pending_slot, clarification_attempts, category, selected_ref, "
    "offered_refs, pending_disputes, last_turn_id, last_case_number, last_ticket_ref, "
    "updated_at_utc"
)

# Selects a session's own row by its primary key; the column list is a module constant, not
# request data, so there is nothing here for a query-builder warning to actually be about.
_SELECT = f"SELECT {_COLUMNS} FROM dialogue_state WHERE session_id = %s"  # noqa: S608


def _row_to_state(session_id: str, row: Any) -> DialogueState:
    """A ``dialogue_state`` row, in ``_COLUMNS`` order, as a ``DialogueState``."""
    (
        version,
        lang,
        phase,
        pending_slot,
        clarification_attempts,
        category,
        selected_ref,
        offered_refs,
        pending_disputes,
        last_turn_id,
        last_case_number,
        last_ticket_ref,
        updated_at,
    ) = row
    return DialogueState(
        session_id=session_id,
        version=version,
        lang=lang,
        phase=ConversationPhase(phase),
        pending_slot=Slot(pending_slot) if pending_slot is not None else None,
        clarification_attempts=clarification_attempts,
        category=DisputeCategory(category) if category is not None else None,
        selected_ref=selected_ref,
        offered_refs=tuple(offered_refs),
        pending_disputes=pending_disputes,
        last_turn_id=last_turn_id,
        last_case_number=last_case_number,
        last_ticket_ref=last_ticket_ref,
        updated_at=updated_at,
    )


def _current(cur: Cursor, session_id: str) -> DialogueState | None:
    """The session's current row, or ``None`` for a fresh conversation."""
    cur.execute(_SELECT, (session_id,))
    row = cur.fetchone()
    return _row_to_state(session_id, row) if row is not None else None


def raise_if_settled(current: DialogueState, expected_version: int, turn_id: str) -> None:
    """Raise when ``current`` already answers this save; otherwise return so the write proceeds.

    Raises
    ------
    DuplicateTurn
        ``current`` was produced by this same ``turn_id``; carries ``current`` to replay.
    Conflict
        ``current.version`` is not ``expected_version``: the caller read a stale state.

    Both outcomes are legitimate here: a proactive check reads the row before any write is
    attempted, so a version that still matches ``expected_version`` is the ordinary case that
    goes on to write.
    """
    if current.last_turn_id == turn_id:
        raise DuplicateTurn(current)
    if current.version != expected_version:
        raise Conflict(
            f"session {current.session_id} is at version {current.version}, not {expected_version}"
        )


def reclassify_lost_race(current: DialogueState, expected_version: int, turn_id: str) -> NoReturn:
    """Classify a save whose ``INSERT ... WHERE version = expected_version`` matched no row.

    Unlike ``raise_if_settled``, this never returns normally: ``current`` was read *after* the
    failed write, so its version cannot equal ``expected_version`` (a version only ever
    increases): the only question is whether the write that beat this one was this same turn
    id (replay it) or a different one (conflict, retry).

    Raises
    ------
    DuplicateTurn
        The winning write carried this ``turn_id``.
    Conflict
        The winning write was a different turn.
    """
    if current.last_turn_id == turn_id:
        raise DuplicateTurn(current)
    raise Conflict(
        f"session {current.session_id} is at version {current.version}, not {expected_version}"
    )


class PostgresDialogueStore:
    """A ``DialogueStore`` backed by the ``dialogue_state`` table."""

    def __init__(self, dsn: str) -> None:
        """Keep the DSN; a connection is opened per ``get`` or ``save`` call."""
        self._dsn = dsn

    def _log_failure(self, event: str, session_id: str) -> None:
        """A store-failure log line, correlated to its HTTP request (no trace id at this layer:
        unlike ``PostgresToolPort``, this store is not constructed per conversation trace)."""
        logger.warning("%s session_id=%s request_id=%s", event, session_id, current_request_id())

    def _log_replay(self, session_id: str, turn_id: str) -> None:
        """Log that a repeated turn id was replayed rather than re-advanced."""
        logger.info(
            "dialogue_turn_replayed session_id=%s turn_id=%s request_id=%s",
            session_id,
            turn_id,
            current_request_id(),
        )

    def _log_conflict(self, session_id: str, expected_version: int) -> None:
        """Log a genuinely stale write, refused rather than silently overwritten."""
        logger.warning(
            "dialogue_state_conflict session_id=%s expected_version=%s request_id=%s",
            session_id,
            expected_version,
            current_request_id(),
        )

    def get(self, session_id: str) -> DialogueState | None:
        """The current state of ``session_id``, or ``None`` for a fresh conversation.

        Raises
        ------
        psycopg.Error
            The store could not be read; logged with the request id, then re-raised.
        """
        try:
            with (
                psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
                conn.cursor() as cur,
            ):
                return _current(cur, session_id)
        except psycopg.Error:
            self._log_failure("dialogue_state_get_failed", session_id)
            raise

    def save(
        self, state: DialogueState, *, expected_version: int, turn_id: str, now: datetime
    ) -> DialogueState:
        """Persist ``state`` as the next version of its session and return the stored state.

        The stored copy carries ``version = expected_version + 1``, ``last_turn_id = turn_id`` and
        ``updated_at = now``. The read and the write happen on one connection, committed when the
        block exits without an error; the ``INSERT ... ON CONFLICT`` updates the row only while
        its version still equals ``expected_version``.

        Raises
        ------
        DuplicateTurn
            ``turn_id`` was already applied; carries the state that turn produced.
        Conflict
            The stored version is no longer ``expected_version``.
        psycopg.Error
            The store could not be reached or the statement failed; logged, then re-raised.
        """
        to_save = state.model_copy(
            update={"version": expected_version + 1, "last_turn_id": turn_id, "updated_at": now}
        )
        try:
            with (
                psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
                conn.cursor() as cur,
            ):
                current = _current(cur, state.session_id)
                if current is not None:
                    raise_if_settled(current, expected_version, turn_id)

                cur.execute(
                    """
                    INSERT INTO dialogue_state (
                        session_id, version, lang, phase, pending_slot, clarification_attempts,
                        category, selected_ref, offered_refs, pending_disputes,
                        last_turn_id, last_case_number, last_ticket_ref, updated_at_utc
                    ) VALUES (
                        %(session_id)s, %(version)s, %(lang)s, %(phase)s, %(pending_slot)s,
                        %(clarification_attempts)s, %(category)s, %(selected_ref)s,
                        %(offered_refs)s, %(pending_disputes)s, %(last_turn_id)s,
                        %(last_case_number)s, %(last_ticket_ref)s, %(updated_at)s
                    )
                    ON CONFLICT (session_id) DO UPDATE SET
                        version = EXCLUDED.version,
                        lang = EXCLUDED.lang,
                        phase = EXCLUDED.phase,
                        pending_slot = EXCLUDED.pending_slot,
                        clarification_attempts = EXCLUDED.clarification_attempts,
                        category = EXCLUDED.category,
                        selected_ref = EXCLUDED.selected_ref,
                        offered_refs = EXCLUDED.offered_refs,
                        pending_disputes = EXCLUDED.pending_disputes,
                        last_turn_id = EXCLUDED.last_turn_id,
                        last_case_number = EXCLUDED.last_case_number,
                        last_ticket_ref = EXCLUDED.last_ticket_ref,
                        updated_at_utc = EXCLUDED.updated_at_utc
                    WHERE dialogue_state.version = %(expected_version)s
                    RETURNING session_id
                    """,
                    {
                        "session_id": to_save.session_id,
                        "version": to_save.version,
                        "lang": to_save.lang,
                        "phase": to_save.phase.value,
                        "pending_slot": to_save.pending_slot.value
                        if to_save.pending_slot is not None
                        else None,
                        "clarification_attempts": to_save.clarification_attempts,
                        "category": to_save.category.value
                        if to_save.category is not None
                        else None,
                        "selected_ref": to_save.selected_ref,
                        "offered_refs": list(to_save.offered_refs),
                        "pending_disputes": to_save.pending_disputes,
                        "last_turn_id": to_save.last_turn_id,
                        "last_case_number": to_save.last_case_number,
                        "last_ticket_ref": to_save.last_ticket_ref,
                        "updated_at": to_save.updated_at,
                        "expected_version": expected_version,
                    },
                )
                won = cur.fetchone() is not None
                if not won:
                    lost_to = _current(cur, state.session_id)
                    if lost_to is None:  # pragma: no cover
                        # Unreachable in practice: a lost race means a conflicting row exists,
                        # and nothing in this design ever deletes a dialogue_state row.
                        raise RuntimeError(f"dialogue_state row for {state.session_id} disappeared")
                    reclassify_lost_race(lost_to, expected_version, turn_id)
        except DuplicateTurn:
            self._log_replay(state.session_id, turn_id)
            raise
        except Conflict:
            self._log_conflict(state.session_id, expected_version)
            raise
        except psycopg.Error:
            self._log_failure("dialogue_state_save_failed", state.session_id)
            raise
        return to_save
