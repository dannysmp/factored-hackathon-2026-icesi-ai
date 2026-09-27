"""
Dialogue Store Tests
====================

Component: ``app.conversation.store``. Hermetic: the store is in-memory and the clock is a
parameter, never read internally.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.conversation.state import DialogueState
from app.conversation.store import Conflict, DuplicateTurn, InMemoryDialogueStore

_T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
_T1 = datetime(2026, 9, 27, 12, 1, tzinfo=UTC)


def _state(**changes: object) -> DialogueState:
    values: dict[str, object] = {"session_id": "s-1", "lang": "es", "updated_at": _T0}
    return DialogueState(**{**values, **changes})


def test_a_fresh_session_has_no_state() -> None:
    """A session the store has never seen returns nothing, not an error."""
    store = InMemoryDialogueStore()

    assert store.get("never-seen") is None


def test_a_saved_state_is_read_back_with_a_bumped_version() -> None:
    """The first save of a session moves it from version 1 to version 2."""
    store = InMemoryDialogueStore()

    saved = store.save(_state(), expected_version=1, turn_id="turn-1", now=_T1)

    assert saved.version == 2
    assert saved.updated_at == _T1
    assert store.get("s-1") == saved


def test_a_save_against_a_stale_version_is_a_conflict() -> None:
    """Two turns reading the same version: the second save loses, cleanly."""
    store = InMemoryDialogueStore()
    store.save(_state(), expected_version=1, turn_id="turn-1", now=_T1)

    with pytest.raises(Conflict):
        store.save(_state(), expected_version=1, turn_id="turn-2", now=_T1)


def test_repeating_a_turn_id_returns_the_state_it_already_produced_not_a_second_advance() -> None:
    """A retried request does not advance the state twice."""
    store = InMemoryDialogueStore()
    first = store.save(_state(), expected_version=1, turn_id="turn-1", now=_T1)

    with pytest.raises(DuplicateTurn) as raised:
        store.save(_state(clarification_attempts=1), expected_version=2, turn_id="turn-1", now=_T1)

    assert raised.value.state == first


def test_a_new_turn_id_after_a_successful_save_advances_normally() -> None:
    """Turn idempotency only blocks a repeat; a genuinely new turn still proceeds."""
    store = InMemoryDialogueStore()
    first = store.save(_state(), expected_version=1, turn_id="turn-1", now=_T1)

    second = store.save(first, expected_version=first.version, turn_id="turn-2", now=_T1)

    assert second.version == first.version + 1
    assert second.last_turn_id == "turn-2"


def test_different_sessions_do_not_interfere() -> None:
    """One session's state and version are independent of another's."""
    store = InMemoryDialogueStore()

    a = store.save(_state(session_id="a"), expected_version=1, turn_id="t-a", now=_T1)
    b = store.save(_state(session_id="b"), expected_version=1, turn_id="t-b", now=_T1)

    assert store.get("a") == a
    assert store.get("b") == b
    assert a.session_id != b.session_id
