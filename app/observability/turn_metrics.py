"""
Turn Metrics Summary
=====================

Overview
--------
Proves the stated observability bar — "cost per case computable from logs alone" — executably:
takes already-parsed ``turn_completed`` JSON log records (``app.conversation.controller``) and
computes latency percentiles and cost rollups, the same computation a CloudWatch Logs Insights
query would run over the real log stream once deployed. Nothing here reads a log file, calls AWS,
or holds any state between calls; it is a pure function over whatever records a caller already
parsed.

Scope
-----
In: ``summarize_turns`` and the percentile it uses.
Out: turning a raw JSON log line into one of the records this function reads. ``json.loads`` only
gets as far as the line's own ``message`` string (the repository-wide logging convention puts
``session_id``, ``case_number`` and the rest inside it as ``key=value`` pairs, not as separate
top-level JSON keys); extracting those — by a CloudWatch Logs Insights ``parse`` pattern, or an
equivalent regex — is the caller's job, untested here, and reading a log file, a CloudWatch
export or anything else AWS-specific is out of scope too.

Design Principles
-----------------
- Records, not a table: no Postgres, no new persisted state. "Persisted" is read here as durable
  in the log stream, not a second store, matching what the acceptance bar itself asks for. This
  function exists so that claim is checked by a test, not just asserted.
- Money stays ``Decimal`` throughout the cost rollups; latency is plain ``float`` (not money).
- Grouping key is ``session_id`` (present on every turn); ``case_number`` is attached to a
  session's rollup only once that session's own records show one, from the filing turn onward —
  most turns never file a case, so ``case_number`` cannot be the primary key.
- Percentiles use linear interpolation between the two nearest ranks (the same method
  ``statistics.quantiles`` and most spreadsheet tools default to); documented here since "p95" is
  ambiguous without naming a method.

Runtime Contract
----------------
``summarize_turns(records: Iterable[Mapping[str, object]]) -> TurnMetricsSummary`` with
``turn_count``, ``latency_p50_ms``, ``latency_p95_ms``, ``cost_by_session`` and ``cost_by_case``
(both ``dict[str, Decimal]``).

Limitations
-----------
A record missing an expected key is treated as a defensive ``0``/``None`` for that field rather
than raising, since a caller reading real log output should not have one malformed line abort an
otherwise-computable summary.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Iterable, Mapping  # Types of the input records
from dataclasses import dataclass, field  # Immutable result
from decimal import Decimal  # Money is never a float


@dataclass(frozen=True, slots=True)
class TurnMetricsSummary:
    """What a batch of ``turn_completed`` records adds up to."""

    turn_count: int
    latency_p50_ms: float
    latency_p95_ms: float
    cost_by_session: dict[str, Decimal] = field(default_factory=dict)
    cost_by_case: dict[str, Decimal] = field(default_factory=dict)


def _percentile(sorted_values: list[float], pct: float) -> float:
    """The ``pct``-th percentile of already-sorted ``sorted_values``, by linear interpolation.

    ``sorted_values`` must be non-empty and sorted ascending; the caller (``summarize_turns``)
    guarantees both.
    """
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100) * (len(sorted_values) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = rank - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def _decimal(value: object) -> Decimal:
    """``value`` as a ``Decimal``, or zero when absent — never a float intermediate."""
    if value is None:
        return Decimal(0)
    return Decimal(str(value))


def summarize_turns(records: Iterable[Mapping[str, object]]) -> TurnMetricsSummary:
    """Latency percentiles and cost, grouped by session and by case, over ``records``.

    Parameters
    ----------
    records : Iterable[Mapping[str, object]]
        Parsed ``turn_completed`` log records (for example, ``json.loads`` of each JSON log
        line already filtered to ``event == "turn_completed"``). Each is expected to carry
        ``session_id``, ``case_number`` (nullable), ``latency_ms`` and ``cost_usd``.
    """
    materialized = list(records)
    latencies = sorted(
        float(str(r["latency_ms"])) for r in materialized if r.get("latency_ms") is not None
    )
    cost_by_session: dict[str, Decimal] = {}
    case_by_session: dict[str, str] = {}
    for record in materialized:
        session_id = record.get("session_id")
        if session_id is None:
            continue
        session_id = str(session_id)
        cost_by_session[session_id] = cost_by_session.get(session_id, Decimal(0)) + _decimal(
            record.get("cost_usd")
        )
        case_number = record.get("case_number")
        if case_number is not None:
            case_by_session[session_id] = str(case_number)

    cost_by_case: dict[str, Decimal] = {}
    for session_id, cost in cost_by_session.items():
        case_number = case_by_session.get(session_id)
        if case_number is not None:
            cost_by_case[case_number] = cost_by_case.get(case_number, Decimal(0)) + cost

    return TurnMetricsSummary(
        turn_count=len(materialized),
        latency_p50_ms=_percentile(latencies, 50) if latencies else 0.0,
        latency_p95_ms=_percentile(latencies, 95) if latencies else 0.0,
        cost_by_session=cost_by_session,
        cost_by_case=cost_by_case,
    )
