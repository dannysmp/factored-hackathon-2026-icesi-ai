"""
Shared Amount Conversion Tests
================================

Component: ``pipelines.amounts``. Hermetic: an in-memory DuckDB connection evaluates the SQL
expressions against a fixture table, so the test proves what the engine actually computes, not
what the Python source merely looks like it says.
"""

from __future__ import annotations

import duckdb
import pytest

from pipelines.amounts import AmountProvenance, usd_amount_expr, usd_amount_provenance_expr


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    """An in-memory connection with a fixture of (amount, currency, amount_usd, exchange_rate)."""
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE fixture (label VARCHAR, amount DOUBLE, currency VARCHAR, "
        "amount_usd DOUBLE, exchange_rate DOUBLE)"
    )
    connection.execute(
        "INSERT INTO fixture VALUES "
        "('reported', 100.0, 'COP', 25.0, 0.25), "
        "('usd_native', 30.0, 'USD', NULL, NULL), "
        "('converted', 100.0, 'COP', NULL, 0.25), "
        "('unknown', 100.0, 'COP', NULL, NULL)"
    )
    return connection


def _rows(conn: duckdb.DuckDBPyConnection) -> dict[str, tuple[float | None, str]]:
    """Every fixture row's resolved USD amount and provenance, keyed by label."""
    amount_sql = usd_amount_expr(
        amount="amount", currency="currency", amount_usd="amount_usd", exchange_rate="exchange_rate"
    )
    provenance_sql = usd_amount_provenance_expr(
        reported_usd="reported_usd", currency="currency", computed_usd="computed_usd"
    )
    result = conn.execute(
        f"""
        WITH resolved AS (
            SELECT label, currency, amount_usd AS reported_usd, {amount_sql} AS computed_usd
            FROM fixture
        )
        SELECT label, computed_usd, {provenance_sql}
        FROM resolved
        """
    ).fetchall()
    return {label: (usd, provenance) for label, usd, provenance in result}


def test_a_reported_amount_is_kept_and_marked_reported(conn: duckdb.DuckDBPyConnection) -> None:
    """The source's own USD figure wins over any conversion, and is marked as such."""
    rows = _rows(conn)

    assert rows["reported"] == (25.0, AmountProvenance.REPORTED)


def test_an_amount_already_in_usd_is_marked_reported(conn: duckdb.DuckDBPyConnection) -> None:
    """A transaction whose own currency is USD needs no conversion and no rate."""
    rows = _rows(conn)

    assert rows["usd_native"] == (30.0, AmountProvenance.REPORTED)


def test_an_amount_converted_at_the_day_rate_is_marked_converted(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """No stated USD figure, but a rate exists: the converted amount is marked converted."""
    rows = _rows(conn)

    assert rows["converted"] == (25.0, AmountProvenance.CONVERTED)


def test_an_amount_with_no_figure_and_no_rate_is_unknown(conn: duckdb.DuckDBPyConnection) -> None:
    """Neither a stated USD figure nor a rate: the amount is unknown, not invented as zero."""
    rows = _rows(conn)

    assert rows["unknown"] == (None, AmountProvenance.UNKNOWN)


def test_the_expressions_accept_arbitrary_column_references(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """The expressions are plain text, so a caller's own aliases (e.g. a table prefix) compose."""
    conn.execute("CREATE VIEW t AS SELECT * FROM fixture")
    amount_sql = usd_amount_expr(
        amount="t.amount",
        currency="t.currency",
        amount_usd="t.amount_usd",
        exchange_rate="t.exchange_rate",
    )

    result = conn.execute(f"SELECT label, {amount_sql} FROM t WHERE label = 'converted'").fetchone()

    assert result == ("converted", 25.0)
