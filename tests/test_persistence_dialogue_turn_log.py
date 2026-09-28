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
def turn_log() -> PostgresDialogueTurnLog:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)
    # TRUNCATE is refused at the store (migration 0008), including for this reset: the session's
    # own replication role is switched off for it, since a trigger created without ENABLE REPLICA
    # or ENABLE ALWAYS does not fire under 'replica' (matching tests/test_persistence_audit.py).
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute("TRUNCATE TABLE dialogue_turn_log")
        conn.commit()
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
