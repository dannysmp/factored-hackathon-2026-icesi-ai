"""
Dialogue Store
==============

Overview
--------
Where dialogue state lives between turns, keyed by session id. A new login starts a new
conversation: the store is asked for state by the session id a sign-in issued, and a
new sign-in issues a new one, so a prior conversation's state is never resumed under it.

Scope
-----
In: the ``DialogueStore`` port and an in-memory implementation, used until the Postgres
conversation store lands.
Out: the state model (``state``) and issuing session ids (the session service).

Design Principles
-----------------
- Optimistic concurrency: a save states the version it read; a stale version is a ``Conflict``,
  never a silent overwrite, so of two concurrent turns on one session, one wins and the other is
  told to retry.
- Turn idempotency: the store remembers the last turn id it saved a state for and refuses to
  advance the state again for a repeated one, handing back the state that turn already produced.
- In-memory only: state does not survive a process restart, and this implementation is never
  pointed at real customer data; it exists for tests and until the durable store lands.

Runtime Contract
----------------
``DialogueStore`` (protocol), ``InMemoryDialogueStore``, ``Conflict``, ``DuplicateTurn``.
"""

from __future__ import annotations

# Standard libraries
from datetime import datetime  # The instant a save is recorded at, passed in by the caller
from threading import Lock  # Guards the in-memory map against concurrent turns
from typing import Protocol  # The store's port

# Local modules
from app.conversation.state import DialogueState  # What is stored


class Conflict(Exception):
    """A save was attempted against a version that is no longer current."""


class DuplicateTurn(Exception):
    """The turn id was already applied; carries the state that turn produced."""

    def __init__(self, state: DialogueState) -> None:
        super().__init__(f"turn already applied for session {state.session_id}")
        self.state = state


class DialogueStore(Protocol):
    """Where dialogue state is read and written between turns."""

    def get(self, session_id: str) -> DialogueState | None:
        """The current state of ``session_id``, or ``None`` for a fresh conversation."""
        ...

    def save(
        self, state: DialogueState, *, expected_version: int, turn_id: str, now: datetime
    ) -> DialogueState:
        """Persist ``state`` as the version after ``expected_version``.

        Raises
        ------
        Conflict
            When the stored version has moved past ``expected_version``.
        DuplicateTurn
            When ``turn_id`` was already applied to this session.
        """
        ...


class InMemoryDialogueStore:
    """A process-local ``DialogueStore``; state is lost on restart."""

    def __init__(self) -> None:
        self._states: dict[str, DialogueState] = {}
        self._lock = Lock()

    def get(self, session_id: str) -> DialogueState | None:
        """The current state of ``session_id``, or ``None`` for a fresh conversation."""
        with self._lock:
            return self._states.get(session_id)

    def save(
        self, state: DialogueState, *, expected_version: int, turn_id: str, now: datetime
    ) -> DialogueState:
        """Persist ``state``, failing on a stale read or handing back a repeated turn's result."""
        with self._lock:
            current = self._states.get(state.session_id)
            if current is not None:
                if current.last_turn_id == turn_id:
                    raise DuplicateTurn(current)
                if current.version != expected_version:
                    raise Conflict(
                        f"session {state.session_id} is at version {current.version}, "
                        f"not {expected_version}"
                    )
            saved = state.model_copy(
                update={
                    "version": expected_version + 1,
                    "last_turn_id": turn_id,
                    "updated_at": now,
                }
            )
            self._states[state.session_id] = saved
            return saved
