"""
Postgres Dialogue Turn Log Tests
=================================

Component: ``app.persistence.dialogue_turn_log``. Needs a real, migrated Postgres — every test is
marked ``integration`` (``make test-integration``), matching ``tests/test_persistence_audit.py``'s
own convention for a store with no in-memory counterpart to share a body with.
"""

from __future__ import annotations

# Standard libraries
import os
from datetime import UTC, datetime

# Third-party libraries
import psycopg
import pytest

# Local modules
from app.domain.policy.models import ReasonCode
from app.persistence.dialogue_turn_log import PostgresDialogueTurnLog
from app.persistence.migrate import apply_migrations
from contracts.service_v1.console import TimelineEntry
from contracts.service_v1.envelope import Intent

_T1 = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
_T2 = datetime(2026, 6, 18, 12, 5, 0, tzinfo=UTC)


def _entry(**changes: object) -> TimelineEntry:
    values: dict[str, object] = {
        "occurred_at": _T1,
        "trace_id": "s-1",
        "intent": Intent.CLARIFY,
        "state_before": "started",
        "state_after": "clarifying",
        "render_mode": "template",
    }
    return TimelineEntry(**{**values, **changes})


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(value)
    # TRUNCATE is refused at the store, including for this reset: the session's
    # own replication role is switched off for it, since a trigger created without ENABLE REPLICA
    # or ENABLE ALWAYS does not fire under 'replica' (matching tests/test_persistence_audit.py).
    with psycopg.connect(value) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute("TRUNCATE TABLE dialogue_turn_log")
        conn.commit()
    return value


@pytest.fixture
def turn_log(dsn: str) -> PostgresDialogueTurnLog:
    return PostgresDialogueTurnLog(dsn)


@pytest.mark.integration
def test_a_recorded_entry_is_read_back_in_the_timeline(turn_log: PostgresDialogueTurnLog) -> None:
    entry = _entry(
        trace_id="s-1",
        intent=Intent.CONFIRM_FILING,
        reason_code=ReasonCode.ELIGIBLE,
        policy_version="2",
    )

    turn_log.record(entry, session_id="s-1", turn_id="t-1")

    assert turn_log.timeline_for("s-1") == (entry,)


@pytest.mark.integration
def test_a_trace_id_with_no_entries_returns_empty(turn_log: PostgresDialogueTurnLog) -> None:
    assert turn_log.timeline_for("unknown") == ()


@pytest.mark.integration
def test_entries_are_ordered_by_when_they_occurred(turn_log: PostgresDialogueTurnLog) -> None:
    later = _entry(occurred_at=_T2, trace_id="s-1")
    earlier = _entry(occurred_at=_T1, trace_id="s-1")
    turn_log.record(later, session_id="s-1", turn_id="t-2")
    turn_log.record(earlier, session_id="s-1", turn_id="t-1")

    assert turn_log.timeline_for("s-1") == (earlier, later)


@pytest.mark.integration
def test_a_repeated_session_and_turn_id_is_a_no_op(turn_log: PostgresDialogueTurnLog) -> None:
    """A retried write for the same turn never doubles an entry, matching the table's own
    ``UNIQUE (session_id, turn_id)`` — the first write wins, a second attempt changes nothing."""
    first = _entry(trace_id="s-1", intent=Intent.CLARIFY)
    second = _entry(trace_id="s-1", intent=Intent.FAREWELL)

    turn_log.record(first, session_id="s-1", turn_id="t-1")
    turn_log.record(second, session_id="s-1", turn_id="t-1")

    assert turn_log.timeline_for("s-1") == (first,)


@pytest.mark.integration
def test_a_null_reason_code_and_policy_version_round_trip(
    turn_log: PostgresDialogueTurnLog,
) -> None:
    entry = _entry(trace_id="s-1", reason_code=None, policy_version=None)

    turn_log.record(entry, session_id="s-1", turn_id="t-1")

    assert turn_log.timeline_for("s-1") == (entry,)


@pytest.mark.integration
def test_entries_from_different_conversations_do_not_mix(
    turn_log: PostgresDialogueTurnLog,
) -> None:
    mine = _entry(trace_id="s-1")
    theirs = _entry(trace_id="s-2")
    turn_log.record(mine, session_id="s-1", turn_id="t-1")
    turn_log.record(theirs, session_id="s-2", turn_id="t-1")

    assert turn_log.timeline_for("s-1") == (mine,)


# -----------------------------------------------------------------------------
# Append-only, enforced at the store (mirrors tests/test_persistence_audit.py)
# -----------------------------------------------------------------------------


@pytest.mark.integration
def test_dialogue_turn_log_refuses_an_update_at_the_store(
    turn_log: PostgresDialogueTurnLog, dsn: str
) -> None:
    """Fails at the store, not only in code."""
    turn_log.record(_entry(trace_id="s-1"), session_id="s-1", turn_id="t-1")

    with (
        psycopg.connect(dsn, autocommit=True) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
    ):
        cur.execute("UPDATE dialogue_turn_log SET render_mode = 'model'")


@pytest.mark.integration
def test_dialogue_turn_log_refuses_a_delete_at_the_store(
    turn_log: PostgresDialogueTurnLog, dsn: str
) -> None:
    """Fails at the store, not only in code."""
    turn_log.record(_entry(trace_id="s-1"), session_id="s-1", turn_id="t-1")

    with (
        psycopg.connect(dsn, autocommit=True) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
    ):
        cur.execute("DELETE FROM dialogue_turn_log")


@pytest.mark.integration
def test_dialogue_turn_log_refuses_a_truncate_at_the_store_even_for_the_table_owner(
    turn_log: PostgresDialogueTurnLog, dsn: str
) -> None:
    """TRUNCATE fires no row-level trigger, so it needs (and has) its own; this project has no
    role separation, so the connecting role is also the table's owner, and Postgres normally lets
    an owner TRUNCATE regardless of any row-level rule."""
    turn_log.record(_entry(trace_id="s-1"), session_id="s-1", turn_id="t-1")

    with (
        psycopg.connect(dsn, autocommit=True) as conn,
        conn.cursor() as cur,
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
    ):
        cur.execute("TRUNCATE TABLE dialogue_turn_log")
