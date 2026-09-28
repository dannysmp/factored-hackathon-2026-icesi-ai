"""
Turn Metrics Summary Tests
============================

Component: ``app.observability.turn_metrics``. Hermetic: pure functions over plain dicts, no
logging, no file I/O, no network — proves E9's "cost per case computable from logs alone"
executably.
"""

from __future__ import annotations

from decimal import Decimal

from app.observability.turn_metrics import summarize_turns


def _record(
    *,
    session_id: str = "sess-1",
    case_number: str | None = None,
    latency_ms: float = 100.0,
    cost_usd: str = "0",
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "case_number": case_number,
        "latency_ms": latency_ms,
        "cost_usd": cost_usd,
    }


def test_an_empty_batch_summarizes_to_zero() -> None:
    summary = summarize_turns([])

    assert summary.turn_count == 0
    assert summary.latency_p50_ms == 0.0
    assert summary.latency_p95_ms == 0.0
    assert summary.cost_by_session == {}
    assert summary.cost_by_case == {}


def test_turn_count_is_the_number_of_records() -> None:
    summary = summarize_turns([_record(), _record(), _record()])

    assert summary.turn_count == 3


def test_a_single_records_latency_is_both_its_own_percentile() -> None:
    summary = summarize_turns([_record(latency_ms=250.0)])

    assert summary.latency_p50_ms == 250.0
    assert summary.latency_p95_ms == 250.0


def test_p50_is_the_median_of_an_odd_sized_sample() -> None:
    summary = summarize_turns([_record(latency_ms=v) for v in (100.0, 200.0, 300.0)])

    assert summary.latency_p50_ms == 200.0


def test_p95_is_close_to_the_top_of_a_larger_sample() -> None:
    records = [_record(latency_ms=float(v)) for v in range(1, 101)]  # 1..100

    summary = summarize_turns(records)

    # Linear interpolation: rank = 0.95 * 99 = 94.05 -> between the 95th and 96th values (95, 96)
    assert summary.latency_p95_ms == 95.05


def test_cost_is_summed_per_session_across_multiple_turns() -> None:
    records = [
        _record(session_id="sess-1", cost_usd="0.001"),
        _record(session_id="sess-1", cost_usd="0.002"),
        _record(session_id="sess-2", cost_usd="0.005"),
    ]

    summary = summarize_turns(records)

    assert summary.cost_by_session == {
        "sess-1": Decimal("0.003"),
        "sess-2": Decimal("0.005"),
    }


def test_a_session_that_never_files_a_case_has_no_cost_by_case_entry() -> None:
    summary = summarize_turns([_record(session_id="sess-1", cost_usd="0.001", case_number=None)])

    assert summary.cost_by_case == {}
    assert summary.cost_by_session == {"sess-1": Decimal("0.001")}


def test_a_sessions_full_cost_is_attributed_to_the_case_its_later_turn_names() -> None:
    """Most of a session's turns (clarification, search) never carry a case_number; the filing
    turn's own case_number still attributes the whole session's cost to that case."""
    records = [
        _record(session_id="sess-1", cost_usd="0.001", case_number=None),
        _record(session_id="sess-1", cost_usd="0.002", case_number=None),
        _record(session_id="sess-1", cost_usd="0.003", case_number="D-1"),
    ]

    summary = summarize_turns(records)

    assert summary.cost_by_case == {"D-1": Decimal("0.006")}
    assert summary.cost_by_session == {"sess-1": Decimal("0.006")}


def test_two_sessions_filing_different_cases_are_kept_separate() -> None:
    records = [
        _record(session_id="sess-1", cost_usd="0.001", case_number="D-1"),
        _record(session_id="sess-2", cost_usd="0.004", case_number="D-2"),
    ]

    summary = summarize_turns(records)

    assert summary.cost_by_case == {"D-1": Decimal("0.001"), "D-2": Decimal("0.004")}


def test_a_record_missing_cost_usd_contributes_zero_not_an_error() -> None:
    summary = summarize_turns([{"session_id": "sess-1", "latency_ms": 50.0}])

    assert summary.cost_by_session == {"sess-1": Decimal(0)}


def test_a_record_missing_latency_is_excluded_from_the_percentile_not_treated_as_zero() -> None:
    records: list[dict[str, object]] = [
        {"session_id": "sess-1", "cost_usd": "0"},
        _record(session_id="sess-2", latency_ms=100.0),
    ]

    summary = summarize_turns(records)

    assert summary.latency_p50_ms == 100.0
