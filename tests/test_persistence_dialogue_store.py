"""
Postgres Dialogue Store Tests
==============================

Component: ``app.persistence.dialogue_store``. The reclassification helpers are pure and hermetic.
The store itself needs a real, migrated Postgres; marked ``integration``, skipped when
``DATABASE_URL`` is not set.

The parity tests run the same bodies against ``InMemoryDialogueStore`` and
``PostgresDialogueStore`` (parametrized by the ``store`` fixture), asserting the two
implementations agree on every case the port's contract names. The genuine two-thread race test is
Postgres-only: only the real store's own ``INSERT ... ON CONFLICT`` statement, not a proactive
check, is what a true race resolves against.
"""

from __future__ import annotations

# Standard libraries
import os
import threading
from datetime import UTC, datetime

# Third-party libraries
import psycopg
import pytest

# Local modules
from app.conversation.state import ConversationPhase, DialogueState
from app.conversation.store import Conflict, DialogueStore, DuplicateTurn, InMemoryDialogueStore
from app.persistence.dialogue_store import (
    PostgresDialogueStore,
    raise_if_settled,
    reclassify_lost_race,
)
from app.persistence.migrate import apply_migrations
from contracts.service_v1.envelope import Slot

_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
_LATER = datetime(2026, 6, 18, 12, 5, 0, tzinfo=UTC)


def _state(**changes: object) -> DialogueState:
    values: dict[str, object] = {"session_id": "s-1", "lang": "es", "updated_at": _NOW}
    return DialogueState(**{**values, **changes})


# -----------------------------------------------------------------------------
# Pure reclassification helpers
# -----------------------------------------------------------------------------


def test_raise_if_settled_returns_when_the_version_still_matches() -> None:
    """The proactive check's ordinary case: nothing has changed, the caller proceeds to write."""
    current = _state(version=3, last_turn_id="t-1")

    raise_if_settled(current, expected_version=3, turn_id="t-2")


def test_raise_if_settled_raises_duplicate_turn_for_a_repeated_id() -> None:
    """A turn id already applied replays that turn's result, whatever version it left."""
    current = _state(version=3, last_turn_id="t-1")

    with pytest.raises(DuplicateTurn) as excinfo:
        raise_if_settled(current, expected_version=3, turn_id="t-1")
    assert excinfo.value.state is current


def test_raise_if_settled_raises_conflict_for_a_stale_version() -> None:
    """A version that has moved on, for a genuinely new turn, is a conflict to retry."""
    current = _state(version=4, last_turn_id="t-1")

    with pytest.raises(Conflict):
        raise_if_settled(current, expected_version=3, turn_id="t-2")


def test_reclassify_lost_race_replays_a_colliding_identical_turn() -> None:
    """Two literally concurrent retries of the same turn: the loser replays the winner's result."""
    winner = _state(version=2, last_turn_id="t-1")

    with pytest.raises(DuplicateTurn) as excinfo:
        reclassify_lost_race(winner, expected_version=1, turn_id="t-1")
    assert excinfo.value.state is winner


def test_reclassify_lost_race_reports_a_conflict_for_a_different_turn() -> None:
    """Two different turns racing: the loser is told to retry, not handed the winner's reply."""
    winner = _state(version=2, last_turn_id="t-1")

    with pytest.raises(Conflict):
        reclassify_lost_race(winner, expected_version=1, turn_id="t-2")


# -----------------------------------------------------------------------------
# Parity: the same behavior, in memory and in Postgres
# -----------------------------------------------------------------------------


@pytest.fixture(params=["memory", "postgres"])
def store(request: pytest.FixtureRequest) -> DialogueStore:
    if request.param == "memory":
        return InMemoryDialogueStore()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE dialogue_state, handoff_actions, handoff_open_questions, "
            "handoff_reason_codes, handoff_sources, handoff_outbox CASCADE"
        )
    return PostgresDialogueStore(dsn)


def test_a_fresh_session_always_succeeds(store: DialogueStore) -> None:
    """The very first save for a session succeeds whatever expected_version names."""
    saved = store.save(_state(), expected_version=0, turn_id="t-1", now=_NOW)

    assert saved.version == 1
    assert saved.last_turn_id == "t-1"
    assert store.get("s-1") == saved


def test_a_matching_version_advances(store: DialogueStore) -> None:
    """A save whose expected_version matches the stored one advances by exactly one."""
    store.save(_state(), expected_version=0, turn_id="t-1", now=_NOW)

    saved = store.save(
        _state(pending_slot=Slot.REASON), expected_version=1, turn_id="t-2", now=_LATER
    )

    assert saved.version == 2
    assert saved.pending_slot is Slot.REASON


def test_a_stale_version_is_a_conflict(store: DialogueStore) -> None:
    """Two turns cannot both advance from the same version; the second is told to retry."""
    store.save(_state(), expected_version=0, turn_id="t-1", now=_NOW)

    with pytest.raises(Conflict):
        store.save(_state(), expected_version=0, turn_id="t-2", now=_LATER)


def test_a_repeated_turn_id_replays_without_advancing(store: DialogueStore) -> None:
    """A retried turn id hands back exactly what that turn already produced, not a new version."""
    first = store.save(
        _state(pending_slot=Slot.TRANSACTION), expected_version=0, turn_id="t-1", now=_NOW
    )

    with pytest.raises(DuplicateTurn) as excinfo:
        store.save(_state(pending_slot=Slot.REASON), expected_version=0, turn_id="t-1", now=_LATER)

    assert excinfo.value.state.version == first.version
    assert excinfo.value.state.pending_slot is Slot.TRANSACTION


def test_get_returns_none_for_a_session_never_saved(store: DialogueStore) -> None:
    """A conversation that has never written anything reads back as absent, not an error."""
    assert store.get("never-seen") is None


def test_a_case_filed_and_a_handoff_survive_a_round_trip(store: DialogueStore) -> None:
    """The idempotent-replay fields persist and read back exactly, in Postgres as in memory."""
    filed = _state().with_case_filed("D-1")
    store.save(filed, expected_version=0, turn_id="t-1", now=_NOW)

    read_back = store.get("s-1")

    assert read_back is not None
    assert read_back.last_case_number == "D-1"
    assert read_back.phase is ConversationPhase.CLOSED


def test_the_offered_transaction_references_survive_a_round_trip_in_order(
    store: DialogueStore,
) -> None:
    """The references shown in a list read back in the order shown; an empty list stays empty."""
    store.save(
        _state(offered_refs=("TX-3", "TX-1", "TX-2")), expected_version=0, turn_id="t-1", now=_NOW
    )
    read_back = store.get("s-1")
    assert read_back is not None
    assert read_back.offered_refs == ("TX-3", "TX-1", "TX-2")

    store.save(_state(version=2, offered_refs=()), expected_version=1, turn_id="t-2", now=_NOW)
    cleared = store.get("s-1")
    assert cleared is not None
    assert cleared.offered_refs == ()


# -----------------------------------------------------------------------------
# Postgres-only: telemetry
# -----------------------------------------------------------------------------


def _postgres_store() -> PostgresDialogueStore:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE dialogue_state CASCADE")
    return PostgresDialogueStore(dsn)


@pytest.mark.integration
def test_a_repeated_turn_id_logs_a_replay_event(caplog: pytest.LogCaptureFixture) -> None:
    """A replay is its own log line, for operational grep alongside the port's own doctrine."""
    store = _postgres_store()
    store.save(_state(), expected_version=0, turn_id="t-1", now=_NOW)

    with (
        caplog.at_level("INFO", logger="app.persistence.dialogue_store"),
        pytest.raises(DuplicateTurn),
    ):
        store.save(_state(), expected_version=0, turn_id="t-1", now=_LATER)

    assert any(
        "dialogue_turn_replayed" in record.message and "session_id=s-1" in record.message
        for record in caplog.records
    )


@pytest.mark.integration
def test_a_stale_version_logs_a_conflict_event(caplog: pytest.LogCaptureFixture) -> None:
    """A stale write is logged before it is raised, not silently retried in the dark."""
    store = _postgres_store()
    store.save(_state(), expected_version=0, turn_id="t-1", now=_NOW)

    with (
        caplog.at_level("WARNING", logger="app.persistence.dialogue_store"),
        pytest.raises(Conflict),
    ):
        store.save(_state(), expected_version=0, turn_id="t-2", now=_LATER)

    assert any(
        "dialogue_state_conflict" in record.message and "session_id=s-1" in record.message
        for record in caplog.records
    )


@pytest.mark.integration
def test_a_get_failure_logs_before_propagating(caplog: pytest.LogCaptureFixture) -> None:
    """A genuine store failure on a read is logged, then still raised — never swallowed."""
    store = PostgresDialogueStore("postgresql://nobody:nowhere@localhost:1/does_not_exist")

    with (
        caplog.at_level("WARNING", logger="app.persistence.dialogue_store"),
        pytest.raises(psycopg.Error),
    ):
        store.get("s-1")

    assert any("dialogue_state_get_failed" in record.message for record in caplog.records)


@pytest.mark.integration
def test_a_save_failure_logs_before_propagating(caplog: pytest.LogCaptureFixture) -> None:
    """A genuine store failure on a write is logged, then still raised — never swallowed."""
    store = PostgresDialogueStore("postgresql://nobody:nowhere@localhost:1/does_not_exist")

    with (
        caplog.at_level("WARNING", logger="app.persistence.dialogue_store"),
        pytest.raises(psycopg.Error),
    ):
        store.save(_state(), expected_version=0, turn_id="t-1", now=_NOW)

    assert any("dialogue_state_save_failed" in record.message for record in caplog.records)


# -----------------------------------------------------------------------------
# Postgres-only: a genuine race, resolved by the store's own constraint
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_two_concurrent_first_saves_resolve_to_exactly_one_winner() -> None:
    """Two threads racing a session's very first save: one wins, the other gets a real conflict.

    Both threads pass expected_version=0 (what a caller who both read ``get()`` as ``None`` would
    pass); only the store's own ``INSERT ... ON CONFLICT`` statement — not any proactive check
    the threads might otherwise agree on before either writes — can be what actually resolves it.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE dialogue_state CASCADE")
    store = PostgresDialogueStore(dsn)
    barrier = threading.Barrier(2)
    outcomes: list[BaseException | DialogueState | None] = [None, None]

    def _attempt(index: int, turn_id: str) -> None:
        barrier.wait()
        try:
            outcomes[index] = store.save(_state(), expected_version=0, turn_id=turn_id, now=_NOW)
        except BaseException as error:  # noqa: BLE001 -- recorded, not swallowed
            outcomes[index] = error

    threads = [
        threading.Thread(target=_attempt, args=(0, "t-a")),
        threading.Thread(target=_attempt, args=(1, "t-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    successes = [o for o in outcomes if isinstance(o, DialogueState)]
    conflicts = [o for o in outcomes if isinstance(o, Conflict)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert store.get("s-1") == successes[0]
