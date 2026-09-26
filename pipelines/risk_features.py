"""
Risk Feature Mart
=================

Overview
--------
Builds the table the transaction risk model learns from: one row per transaction with features
that were knowable when the transaction happened, the fraud label, and the training, validation or
test period the row belongs to. It also writes ``reports/risk-features.md`` with the size of each
period, how much of each feature is present and which source columns were deliberately left out.

Scope
-----
In: reading the cleaned layer, deriving the features, labelling the periods, the manifest and the
report, and the command line ``python -m pipelines.risk_features``.
Out: training or evaluating a model.

Design Principles
-----------------
- **No information from the future.** Every feature of a transaction is computed from that
  transaction and from earlier transactions of the same customer only; the velocity windows end
  strictly before the transaction. A test appends later transactions and changes later labels and
  shows that earlier rows do not move.
- **Only what an authorisation system would know.** The outcome of the transaction (its status and
  response code) and the source's own fraud score are excluded: they are produced after, or from,
  the fraud decision the model is meant to predict.
- **Periods come from one file** (``models/split.toml``), so the model is always evaluated on days
  after the ones it learned from.
- **No identifiers in the mart** except the transaction identifier; the customer is used to
  compute features and does not appear in the output.
- Deterministic: rows are ordered by time and identifier, so the same cleaned layer and split give
  byte-identical files.

Runtime Contract
----------------
``load_split(path) -> SplitConfig``
``build_risk_features(silver_dir, gold_dir, split, *, code_version) -> RiskFeaturesManifest``
``render_report(manifest) -> str``

Limitations
-----------
The distance feature measures movement from the customer's previous located transaction, not from
home: the registration branch of almost every customer never resolves to a branch (5 of 150,000),
so there is no home to measure from. It exists only for the share of transactions that carry
coordinates. The amount in US dollars is converted with the day's rate when the source does not
state it. The label is the source's synthetic ``is_fraud``.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command line
import hashlib  # Digests of inputs and outputs
import json  # Manifest serialisation
import logging  # Progress events
import os  # Atomic replacement of files
import tempfile  # Working database that never outlives the build
import tomllib  # Reading the split file
from collections.abc import Sequence  # Argument type of main
from dataclasses import dataclass  # Immutable configuration and manifest objects
from datetime import date  # Boundaries of the periods
from pathlib import Path  # Locations
from typing import Any  # Manifest contents

# Third-party libraries
import duckdb  # Window functions over the cleaned Parquet files

# Local modules
from pipelines.raw import quote_literal  # Safe SQL string literals
from pipelines.silver import git_version  # Same code-version rule as the cleaning stage

logger = logging.getLogger(__name__)

DEFAULT_SPLIT = Path(__file__).resolve().parents[1] / "models" / "split.toml"
SILVER_INPUTS = ("transactions", "customers", "daily_exchange_rates")
MART_NAME = "risk_features.parquet"
MANIFEST_NAME = "manifest.json"
SPLITS = ("train", "validation", "test")

# Features in column order, with what each one is. Categorical features are text, the rest numbers.
FEATURES: dict[str, str] = {
    "amount_usd": "amount in US dollars, as stated or converted with the day's rate",
    "amount_usd_source": "reported, converted or unavailable",
    "currency": "currency of the transaction",
    "channel": "channel the transaction came through",
    "transaction_type": "purchase, withdrawal, transfer, payment, deposit or adjustment",
    "merchant_category": "category of the merchant, `unknown` when not stated",
    "transaction_country": "country where the transaction happened",
    "customer_country": "country of the customer's address",
    "country_mismatch": "the two countries differ (empty when either is unknown)",
    "hour": "hour of the day of the transaction",
    "day_of_week": "day of the week, 1 (Monday) to 7 (Sunday)",
    "is_weekend": "Saturday or Sunday",
    "tx_count_24h": "the customer's transactions in the 24 hours before",
    "tx_sum_usd_24h": "their total in US dollars in the 24 hours before",
    "tx_count_7d": "the customer's transactions in the 7 days before",
    "tx_sum_usd_7d": "their total in US dollars in the 7 days before",
    "seconds_since_previous": (
        "seconds since the customer's previous transaction (empty for the first)"
    ),
    "distance_previous_km": (
        "kilometres from where the customer's previous transaction with coordinates took place "
        "(empty when this one has no coordinates or there is no earlier one)"
    ),
}

# Source columns left out on purpose, and why.
EXCLUDED: dict[str, str] = {
    "fraud_score": (
        "the source's own fraud score: it is not an input known beforehand but the product of the "
        "label or of a detector that already ran (see the evidence below)"
    ),
    "response_code": "the authorisation outcome, known only after the decision the model informs",
    "transaction_status": "the outcome (approved, declined, pending, reversed), known only after",
    "is_fraud": "the label; present in the mart only as the target, never as a feature",
    "customer_id, product_id": "identifiers: the model must generalise across customers",
}


@dataclass(frozen=True, slots=True)
class SplitConfig:
    """Last day of the training period and of the validation period."""

    train_end: date
    validation_end: date


def load_split(path: Path = DEFAULT_SPLIT) -> SplitConfig:
    """Read and validate the split file.

    Raises
    ------
    ValueError
        When a date is missing, is not a date, or the boundaries are not in order.
    OSError
        When the file cannot be read.
    """
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    try:
        train_end, validation_end = document["train_end"], document["validation_end"]
    except KeyError as missing:
        raise ValueError(f"{missing} is missing from {path.name}") from None
    if not all(isinstance(value, date) for value in (train_end, validation_end)):
        raise ValueError("train_end and validation_end must be dates")
    if not train_end < validation_end:
        raise ValueError("train_end must be before validation_end")
    return SplitConfig(train_end, validation_end)


@dataclass(frozen=True, slots=True)
class RiskFeaturesManifest:
    """What the build read and wrote."""

    code_version: str
    inputs: dict[str, str]
    split: SplitConfig
    rows: dict[str, int]
    positives: dict[str, int]
    coverage: dict[str, int]
    fraud_score_evidence: dict[str, Any]
    output_sha256: str

    def as_dict(self) -> dict[str, Any]:
        """The manifest as JSON-serialisable data."""
        return {
            "code_version": self.code_version,
            "inputs": self.inputs,
            "split": {
                "train_end": self.split.train_end.isoformat(),
                "validation_end": self.split.validation_end.isoformat(),
            },
            "rows": self.rows,
            "positives": self.positives,
            "coverage": self.coverage,
            "features": list(FEATURES),
            "excluded": list(EXCLUDED),
            "fraud_score_evidence": self.fraud_score_evidence,
            "output_sha256": self.output_sha256,
        }


def _sha256(path: Path) -> str:
    """SHA-256 of a file, read in one pass."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _query(split: SplitConfig) -> str:
    """The SQL of the mart. Windows end strictly before the transaction, per customer."""
    train_end = quote_literal(split.train_end.isoformat())
    validation_end = quote_literal(split.validation_end.isoformat())
    return f"""
    WITH rates AS (
        SELECT date, source_currency, exchange_rate
        FROM daily_exchange_rates
        WHERE target_currency = 'USD'
    ),
    home AS (SELECT customer_id, country AS customer_country FROM customers),
    enriched AS (
        SELECT
            t.transaction_id,
            t.customer_id,
            t.transaction_date AS transaction_ts,
            t.currency,
            t.channel,
            t.transaction_type,
            coalesce(t.merchant_category, 'unknown') AS merchant_category,
            t.transaction_country,
            h.customer_country,
            t.latitude,
            t.longitude,
            t.is_fraud,
            t.amount_usd AS reported_usd,
            CASE
                WHEN t.amount_usd IS NOT NULL THEN CAST(t.amount_usd AS DOUBLE)
                WHEN t.currency = 'USD' THEN CAST(t.amount AS DOUBLE)
                WHEN r.exchange_rate IS NOT NULL
                    THEN round(CAST(t.amount AS DOUBLE) * CAST(r.exchange_rate AS DOUBLE), 2)
            END AS amount_usd
        FROM transactions AS t
        LEFT JOIN rates AS r
            ON r.date = CAST(t.transaction_date AS DATE) AND r.source_currency = t.currency
        LEFT JOIN home AS h ON h.customer_id = t.customer_id
    )
    SELECT
        transaction_id,
        transaction_ts,
        CASE
            WHEN CAST(transaction_ts AS DATE) <= DATE {train_end} THEN 'train'
            WHEN CAST(transaction_ts AS DATE) <= DATE {validation_end} THEN 'validation'
            ELSE 'test'
        END AS split,
        is_fraud,
        amount_usd,
        CASE
            WHEN reported_usd IS NOT NULL THEN 'reported'
            WHEN amount_usd IS NOT NULL THEN 'converted'
            ELSE 'unavailable'
        END AS amount_usd_source,
        currency,
        channel,
        transaction_type,
        merchant_category,
        transaction_country,
        customer_country,
        transaction_country <> customer_country AS country_mismatch,
        CAST(hour(transaction_ts) AS INTEGER) AS hour,
        CAST(isodow(transaction_ts) AS INTEGER) AS day_of_week,
        isodow(transaction_ts) >= 6 AS is_weekend,
        CAST(count(*) OVER w24 AS INTEGER) AS tx_count_24h,
        coalesce(sum(amount_usd) OVER w24, 0.0) AS tx_sum_usd_24h,
        CAST(count(*) OVER w7 AS INTEGER) AS tx_count_7d,
        coalesce(sum(amount_usd) OVER w7, 0.0) AS tx_sum_usd_7d,
        date_diff('second', lag(transaction_ts) OVER ordered, transaction_ts)
            AS seconds_since_previous,
        CASE
            WHEN latitude IS NOT NULL AND longitude IS NOT NULL AND previous_place IS NOT NULL
            THEN round(2 * 6371.0 * asin(sqrt(
                pow(sin(radians(CAST(latitude - previous_place.lat AS DOUBLE)) / 2), 2)
                + cos(radians(CAST(previous_place.lat AS DOUBLE)))
                * cos(radians(CAST(latitude AS DOUBLE)))
                * pow(sin(radians(CAST(longitude - previous_place.lon AS DOUBLE)) / 2), 2)
            )), 3)
        END AS distance_previous_km
    FROM (
        SELECT *,
            last_value(
                CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL
                     THEN struct_pack(lat := latitude, lon := longitude) END
                IGNORE NULLS
            ) OVER earlier AS previous_place
        FROM enriched
        WINDOW earlier AS (
            PARTITION BY customer_id ORDER BY transaction_ts, transaction_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        )
    )
    WINDOW
        ordered AS (PARTITION BY customer_id ORDER BY transaction_ts, transaction_id),
        w24 AS (
            PARTITION BY customer_id ORDER BY transaction_ts
            RANGE BETWEEN INTERVAL 24 HOURS PRECEDING AND INTERVAL 1 MICROSECOND PRECEDING
        ),
        w7 AS (
            PARTITION BY customer_id ORDER BY transaction_ts
            RANGE BETWEEN INTERVAL 7 DAYS PRECEDING AND INTERVAL 1 MICROSECOND PRECEDING
        )
    ORDER BY transaction_ts, transaction_id
    """


def build_risk_features(
    silver_dir: Path, gold_dir: Path, split: SplitConfig, *, code_version: str
) -> RiskFeaturesManifest:
    """Write the mart and its manifest under ``gold_dir`` and return the manifest.

    Raises
    ------
    FileNotFoundError
        When a cleaned table the mart reads is missing.
    """
    # Refuse to build from a partial cleaned layer
    inputs: dict[str, str] = {}
    for name in SILVER_INPUTS:
        path = silver_dir / "silver" / f"{name}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"cleaned table {name} not found; run the cleaning stage first")
        inputs[name] = _sha256(path)

    gold_dir.mkdir(parents=True, exist_ok=True)
    target = gold_dir / MART_NAME
    temporary = target.with_suffix(".parquet.tmp")
    with tempfile.TemporaryDirectory() as workdir:
        con = duckdb.connect(str(Path(workdir) / "features.duckdb"))
        try:
            con.execute(f"PRAGMA temp_directory={quote_literal(workdir)}")
            for name in SILVER_INPUTS:
                source = quote_literal(str(silver_dir / "silver" / f"{name}.parquet"))
                con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet({source})")
            con.execute(
                f"COPY ({_query(split)}) TO {quote_literal(str(temporary))} (FORMAT PARQUET)"
            )
            os.replace(temporary, target)
            rows, positives, coverage, evidence = _measure(con, target)
        finally:
            con.close()

    manifest = RiskFeaturesManifest(
        code_version=code_version,
        inputs=inputs,
        split=split,
        rows=rows,
        positives=positives,
        coverage=coverage,
        fraud_score_evidence=evidence,
        output_sha256=_sha256(target),
    )
    text = json.dumps(manifest.as_dict(), indent=2, sort_keys=True) + "\n"
    manifest_temporary = gold_dir / f"{MANIFEST_NAME}.tmp"
    manifest_temporary.write_text(text, encoding="utf-8")
    os.replace(manifest_temporary, gold_dir / MANIFEST_NAME)
    return manifest


def _measure(
    con: duckdb.DuckDBPyConnection, mart: Path
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, Any]]:
    """Rows and positives per period, non-empty count per feature, and the fraud-score evidence."""
    source = f"read_parquet({quote_literal(str(mart))})"
    rows = dict.fromkeys(SPLITS, 0)
    positives = dict.fromkeys(SPLITS, 0)
    for period, count, fraud in con.execute(
        f"SELECT split, count(*), count(*) FILTER (WHERE is_fraud) FROM {source} GROUP BY split"
    ).fetchall():
        rows[period], positives[period] = int(count), int(fraud)
    counts = ", ".join(f"count({name})" for name in FEATURES)
    (row,) = con.execute(f"SELECT {counts} FROM {source}").fetchall()
    coverage = {name: int(value) for name, value in zip(FEATURES, row, strict=True)}

    # Evidence for excluding the source's fraud score: how well it separates the label
    (maximum_clean,) = con.execute(
        "SELECT max(fraud_score) FROM transactions WHERE NOT is_fraud"
    ).fetchone() or (None,)
    (above, total) = con.execute(
        "SELECT count(*) FILTER (WHERE fraud_score > ?), count(*) FROM transactions WHERE is_fraud",
        [maximum_clean if maximum_clean is not None else 0],
    ).fetchone() or (0, 0)
    evidence = {
        "highest_score_when_not_fraud": None if maximum_clean is None else float(maximum_clean),
        "fraud_rows": int(total),
        "fraud_rows_above_that": int(above),
    }
    return rows, positives, coverage, evidence


# -----------------------------------------------------------------------------
# Report
# -----------------------------------------------------------------------------


def _table(headers: list[str], body: list[list[str]]) -> str:
    """Markdown table."""
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def render_report(manifest: RiskFeaturesManifest) -> str:
    """Render ``reports/risk-features.md`` from the manifest (a pure function of it)."""
    total = sum(manifest.rows.values())
    positives = sum(manifest.positives.values())
    split_rows = []
    for period, until in zip(
        SPLITS,
        (
            f"up to and including {manifest.split.train_end}",
            f"after {manifest.split.train_end}, through {manifest.split.validation_end}",
            f"after {manifest.split.validation_end}",
        ),
        strict=True,
    ):
        rows, fraud = manifest.rows[period], manifest.positives[period]
        split_rows.append(
            [
                period,
                until,
                f"{rows:,}",
                f"{fraud:,}",
                f"{fraud / rows * 100:.3f} %" if rows else "n/a",
            ]
        )
    coverage_rows = [
        [
            f"`{name}`",
            description,
            f"{manifest.coverage[name] / total * 100:.2f} %" if total else "n/a",
        ]
        for name, description in FEATURES.items()
    ]
    excluded_rows = [[f"`{name}`", reason] for name, reason in EXCLUDED.items()]
    evidence = manifest.fraud_score_evidence
    share = (
        f"{evidence['fraud_rows_above_that'] / evidence['fraud_rows'] * 100:.1f} %"
        if evidence["fraud_rows"]
        else "n/a"
    )
    return (
        "\n".join(
            [
                "# Risk features",
                "",
                "The table the transaction risk model learns from: one row per transaction, with "
                "features known when it happened, the fraud label and the period of the row. "
                "Every figure is computed by `make features` from the cleaned layer.",
                "",
                "## 1. Periods",
                "",
                f"{total:,} transactions, {positives:,} labelled fraud "
                f"({positives / total * 100:.3f} % overall)."
                if total
                else "No transactions.",
                "",
                _table(["Period", "Days", "Transactions", "Fraud", "Prevalence"], split_rows),
                "",
                "The boundaries are in `models/split.toml`; the model is evaluated on a period "
                "that follows the days it learned from.",
                "",
                "## 2. Features and how much of each is present",
                "",
                "Each feature of a transaction uses that transaction and the customer's earlier "
                "transactions only; the windows end strictly before it.",
                "",
                _table(["Feature", "Meaning", "Present"], coverage_rows),
                "",
                "## 3. Left out on purpose",
                "",
                _table(["Source column", "Why"], excluded_rows),
                "",
                "Evidence for the fraud score: no transaction that is not fraud scores above "
                f"{evidence['highest_score_when_not_fraud']}, while {share} of the fraud "
                "transactions "
                "do, so the score carries the label and is left out.",
                "",
                "## 4. Lineage",
                "",
                _table(
                    ["Cleaned table", "SHA-256"],
                    [[name, digest[:12]] for name, digest in sorted(manifest.inputs.items())],
                ),
                "",
                f"Output digest `{manifest.output_sha256[:12]}`.",
            ]
        )
        + "\n"
    )


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Build the mart and write the report; return the exit code."""
    parser = argparse.ArgumentParser(description="Build the risk feature mart and its report.")
    parser.add_argument("--silver", type=Path, default=Path("data/silver"))
    parser.add_argument("--gold", type=Path, default=Path("data/gold/risk_features"))
    parser.add_argument("--report", type=Path, default=Path("reports/risk-features.md"))
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--code-version", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        split = load_split(args.split)
        manifest = build_risk_features(
            args.silver, args.gold, split, code_version=args.code_version or git_version()
        )
    except (FileNotFoundError, ValueError, OSError, duckdb.Error) as error:
        logger.error("risk_features_failed reason=%s", type(error).__name__)
        return 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(".md.tmp")
    temporary.write_text(render_report(manifest), encoding="utf-8")
    os.replace(temporary, args.report)
    logger.info("risk_features_written rows=%d report=%s", sum(manifest.rows.values()), args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
