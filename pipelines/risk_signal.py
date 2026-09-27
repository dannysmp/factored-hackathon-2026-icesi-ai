"""
Risk Signal Tabulation
======================

Overview
--------
Tabulates how often a transaction is fraud across the bands of the three features a routing rule
could plausibly use: the amount, the hour of the day and the customer's recent activity. The
report is the first look at whether any of them carries signal, before a model is fitted.

Scope
-----
In: reading the risk feature mart, banding the amount, hour and velocity features, counting rows and
fraud per band for the training and validation periods, the report and the command line
``python -m pipelines.risk_signal``.
Out: fitting or scoring any model.

Design Principles
-----------------
- **The test period is never read.** Only training and validation rows are counted, so this look
  cannot influence any choice that the test period is later used to judge.
- **Bands come from the training period.** Amount edges are training deciles, fixed before the
  validation counts are read; the other features use fixed, round edges.
- **Static queries.** The SQL text is constant; the file location and the band edges are bound as
  parameters, never formatted into the statement.
- Deterministic: bands are ordered by their position, so the same mart gives the same report.

Runtime Contract
----------------
``tabulate_signal(mart) -> SignalTabulation``
``render_report(tabulation) -> str``

Limitations
-----------
Marginal prevalence per band cannot show an interaction between features; the model probe
covers that. Bands with few positives are noisy and are shown with their counts.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command line
import logging  # Progress events
import os  # Atomic replacement of the report
from collections.abc import Sequence  # Argument type of main
from dataclasses import dataclass  # Immutable result objects
from itertools import pairwise  # Neighbouring band edges
from pathlib import Path  # Locations

# Third-party libraries
import duckdb  # Reading the mart

logger = logging.getLogger(__name__)

DEFAULT_MART = Path("data/gold/risk_features/risk_features.parquet")
PERIODS = ("train", "validation")
MISSING = -1  # Band index of a row whose feature is empty

# Deciles of the training amounts, computed once and used as the band edges.
_AMOUNT_EDGES = (
    "SELECT quantile_cont(amount_usd, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]) "
    "FROM read_parquet(?) WHERE split = 'train' AND amount_usd IS NOT NULL"
)

# One constant query per feature: the band index of each row, then rows and fraud per period.
_BAND_QUERIES: dict[str, str] = {
    "amount_usd": (
        "SELECT split, CASE WHEN amount_usd IS NULL THEN -1 "
        "ELSE len(list_filter(?, e -> e <= amount_usd)) END AS band, "
        "count(*), sum(is_fraud::INTEGER) FROM read_parquet(?) "
        "WHERE split IN ('train', 'validation') GROUP BY 1, 2"
    ),
    "hour": (
        "SELECT split, CASE WHEN hour IS NULL THEN -1 "
        "ELSE len(list_filter(?, e -> e <= hour)) END AS band, "
        "count(*), sum(is_fraud::INTEGER) FROM read_parquet(?) "
        "WHERE split IN ('train', 'validation') GROUP BY 1, 2"
    ),
    "tx_count_24h": (
        "SELECT split, CASE WHEN tx_count_24h IS NULL THEN -1 "
        "ELSE len(list_filter(?, e -> e <= tx_count_24h)) END AS band, "
        "count(*), sum(is_fraud::INTEGER) FROM read_parquet(?) "
        "WHERE split IN ('train', 'validation') GROUP BY 1, 2"
    ),
    "tx_count_7d": (
        "SELECT split, CASE WHEN tx_count_7d IS NULL THEN -1 "
        "ELSE len(list_filter(?, e -> e <= tx_count_7d)) END AS band, "
        "count(*), sum(is_fraud::INTEGER) FROM read_parquet(?) "
        "WHERE split IN ('train', 'validation') GROUP BY 1, 2"
    ),
    "seconds_since_previous": (
        "SELECT split, CASE WHEN seconds_since_previous IS NULL THEN -1 "
        "ELSE len(list_filter(?, e -> e <= seconds_since_previous)) END AS band, "
        "count(*), sum(is_fraud::INTEGER) FROM read_parquet(?) "
        "WHERE split IN ('train', 'validation') GROUP BY 1, 2"
    ),
}

# Fixed edges of the features that are not banded by quantiles, and the empty-value wording.
_FIXED_EDGES: dict[str, tuple[float, ...]] = {
    "hour": tuple(float(hour) for hour in range(1, 24)),
    "tx_count_24h": (1.0, 2.0, 3.0),
    "tx_count_7d": (1.0, 3.0, 6.0),
    "seconds_since_previous": (3600.0, 86400.0, 604800.0),
}
_MISSING_LABEL: dict[str, str] = {
    "amount_usd": "unavailable",
    "hour": "unknown",
    "tx_count_24h": "unknown",
    "tx_count_7d": "unknown",
    "seconds_since_previous": "no earlier transaction",
}
_FEATURE_NOTES: dict[str, str] = {
    "amount_usd": "Amount in US dollars; the bands are training deciles.",
    "hour": "Hour of the day.",
    "tx_count_24h": "The customer's transactions in the 24 hours before.",
    "tx_count_7d": "The customer's transactions in the 7 days before.",
    "seconds_since_previous": "Seconds since the customer's previous transaction.",
}


@dataclass(frozen=True, slots=True)
class Band:
    """One band of a feature with the rows and fraud counted in each period."""

    label: str
    counts: dict[str, tuple[int, int]]


@dataclass(frozen=True, slots=True)
class FeatureTable:
    """The bands of one feature, in order."""

    feature: str
    note: str
    bands: tuple[Band, ...]


@dataclass(frozen=True, slots=True)
class SignalTabulation:
    """The prevalence tables of every feature."""

    tables: tuple[FeatureTable, ...]


def _number(value: float) -> str:
    """``value`` without a trailing ``.0`` and with a thousands separator."""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _labels(edges: tuple[float, ...]) -> list[str]:
    """Band labels for ``edges``: below the first, between neighbours, from the last upward."""
    if not edges:
        return ["all values"]
    labels = [f"below {_number(edges[0])}"]
    labels += [f"{_number(low)} to below {_number(high)}" for low, high in pairwise(edges)]
    labels.append(f"{_number(edges[-1])} or more")
    return labels


def _tabulate(
    con: duckdb.DuckDBPyConnection, mart: str, feature: str, edges: tuple[float, ...]
) -> FeatureTable:
    """Count rows and fraud per band of ``feature`` for the training and validation periods."""
    found = con.execute(_BAND_QUERIES[feature], [list(edges), mart]).fetchall()
    counts: dict[int, dict[str, tuple[int, int]]] = {}
    for period, band, rows, fraud in found:
        counts.setdefault(int(band), {})[str(period)] = (int(rows), int(fraud or 0))
    labels = [str(hour) for hour in range(24)] if feature == "hour" else _labels(edges)
    ordered = [(index, labels[index]) for index in range(len(labels))]
    if MISSING in counts:
        ordered.append((MISSING, _MISSING_LABEL[feature]))
    bands = tuple(
        Band(label, {period: counts.get(index, {}).get(period, (0, 0)) for period in PERIODS})
        for index, label in ordered
    )
    return FeatureTable(feature, _FEATURE_NOTES[feature], bands)


def tabulate_signal(mart: Path) -> SignalTabulation:
    """Tabulate fraud prevalence per band of each feature from the mart at ``mart``.

    Raises
    ------
    FileNotFoundError
        When the mart does not exist.
    """
    if not mart.is_file():
        raise FileNotFoundError(f"risk feature mart not found at {mart}; run make features first")
    location = str(mart)
    con = duckdb.connect()
    try:
        row = con.execute(_AMOUNT_EDGES, [location]).fetchone()
        deciles = row[0] if row and row[0] else []
        amount_edges = tuple(sorted({float(edge) for edge in deciles}))
        edges = {"amount_usd": amount_edges, **_FIXED_EDGES}
        tables = tuple(_tabulate(con, location, name, edges[name]) for name in _BAND_QUERIES)
    finally:
        con.close()
    return SignalTabulation(tables)


# -----------------------------------------------------------------------------
# Report
# -----------------------------------------------------------------------------


def _prevalence(rows: int, fraud: int) -> str:
    """Fraud share of ``rows`` as a percentage, or ``n/a`` when there are none."""
    return f"{fraud / rows * 100:.3f} %" if rows else "n/a"


def render_report(tabulation: SignalTabulation) -> str:
    """Render ``reports/risk-signal.md`` from the tabulation (a pure function of it)."""
    totals = {
        period: (
            sum(band.counts[period][0] for band in tabulation.tables[0].bands),
            sum(band.counts[period][1] for band in tabulation.tables[0].bands),
        )
        for period in PERIODS
    }
    lines = [
        "# Risk signal tabulation",
        "",
        "Fraud prevalence per band of the amount, hour and velocity features, in the training and "
        "validation periods. The test period is not read. A feature carries marginal signal only "
        "if prevalence moves clearly across its bands and in the same direction in both periods.",
        "",
        "| Period | Transactions | Fraud | Prevalence |",
        "|---|---|---|---|",
        *[
            f"| {period} | {rows:,} | {fraud:,} | {_prevalence(rows, fraud)} |"
            for period, (rows, fraud) in totals.items()
        ],
        "",
    ]
    for table in tabulation.tables:
        lines += [f"## {table.feature}", "", table.note, ""]
        lines += [
            "| Band | Train rows | Train fraud | Train prevalence "
            "| Validation rows | Validation fraud | Validation prevalence |",
            "|---|---|---|---|---|---|---|",
        ]
        for band in table.bands:
            cells = [
                f"{band.counts[period][0]:,} | {band.counts[period][1]:,} | "
                f"{_prevalence(*band.counts[period])}"
                for period in PERIODS
            ]
            lines.append(f"| {band.label} | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Tabulate the signal and write the report; return the exit code."""
    parser = argparse.ArgumentParser(description="Tabulate fraud prevalence per feature band.")
    parser.add_argument("--mart", type=Path, default=DEFAULT_MART)
    parser.add_argument("--report", type=Path, default=Path("reports/risk-signal.md"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        tabulation = tabulate_signal(args.mart)
    except (FileNotFoundError, duckdb.Error) as error:
        logger.error("risk_signal_failed reason=%s", type(error).__name__)
        return 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(".md.tmp")
    temporary.write_text(render_report(tabulation), encoding="utf-8")
    os.replace(temporary, args.report)
    logger.info("risk_signal_written tables=%d report=%s", len(tabulation.tables), args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
