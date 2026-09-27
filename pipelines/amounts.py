"""
Shared Amount Conversion
=========================

Overview
--------
The one expression of "the transaction's amount in US dollars, and where it came from" (CR-1).
It is a source's own figure when stated, a same-day conversion when a rate exists, and unknown
only when neither is available. Every consumer that needs this figure — the risk-feature mart and
the operational seed — builds it from this module, so the same transaction never gets two answers.

Scope
-----
In: the two SQL expressions (the USD amount, its provenance) and the closed set of provenance
values.
Out: the exchange-rate table itself (``daily_exchange_rates``, read by the caller's own query),
the policy's own use of the figure (``app.domain.policy``, which receives the resolved value, not
this module's SQL).

Design Principles
-----------------
- One implementation, expressed in SQL because both consumers already build the transaction's
  other columns in SQL (DuckDB): a Python reimplementation next to it would let the two drift.
- The provenance expression reads the already-computed columns (the source's own figure, its own
  currency, and the resolved amount), matching the two-step CTE pattern both consumers already
  use: compute the raw and the resolved figure once, then classify which one answered. A
  transaction already in USD is reported, not converted: nothing was converted for it.
- Closed set, added to but never renamed (``AmountProvenance``), matching the same three values
  the service contracts and the serving-store migration already freeze.

Runtime Contract
-----------------
``usd_amount_expr(amount, currency, amount_usd, exchange_rate) -> str`` (a SQL expression);
``usd_amount_provenance_expr(reported_usd, currency, computed_usd) -> str`` (a SQL expression);
each parameter is the column reference the caller's own query already has.

Limitations
-----------
Each expression takes column references as plain strings, not identifiers checked against a
schema; a caller passes a name it does not have and gets a SQL error from the engine, not from
this module.
"""

from __future__ import annotations

from enum import StrEnum  # The closed set of provenance values


class AmountProvenance(StrEnum):
    """Where a resolved USD amount came from."""

    REPORTED = "reported"
    CONVERTED = "converted"
    UNKNOWN = "unknown"


def usd_amount_expr(*, amount: str, currency: str, amount_usd: str, exchange_rate: str) -> str:
    """The SQL expression for the resolved USD amount.

    The source's own figure when stated; the amount is already in USD when its currency says so;
    otherwise the amount converted at the day's rate when one exists; ``NULL`` otherwise.
    """
    return (
        f"CASE "
        f"WHEN {amount_usd} IS NOT NULL THEN CAST({amount_usd} AS DOUBLE) "
        f"WHEN {currency} = 'USD' THEN CAST({amount} AS DOUBLE) "
        f"WHEN {exchange_rate} IS NOT NULL "
        f"THEN round(CAST({amount} AS DOUBLE) * CAST({exchange_rate} AS DOUBLE), 2) "
        f"END"
    )


def usd_amount_provenance_expr(*, reported_usd: str, currency: str, computed_usd: str) -> str:
    """The SQL expression for the resolved amount's provenance.

    ``reported_usd`` and ``computed_usd`` are column references the caller's query already
    computed: the source's own (possibly absent) USD figure, and the amount `usd_amount_expr`
    resolved from it. Reported when the source stated the figure directly, or its own transaction
    is already in USD (no conversion happened, so it is not "converted" either); converted only
    when a day-rate conversion is what produced the figure; unknown when neither.
    """
    return (
        f"CASE "
        f"WHEN {reported_usd} IS NOT NULL THEN '{AmountProvenance.REPORTED}' "
        f"WHEN {currency} = 'USD' THEN '{AmountProvenance.REPORTED}' "
        f"WHEN {computed_usd} IS NOT NULL THEN '{AmountProvenance.CONVERTED}' "
        f"ELSE '{AmountProvenance.UNKNOWN}' "
        f"END"
    )
