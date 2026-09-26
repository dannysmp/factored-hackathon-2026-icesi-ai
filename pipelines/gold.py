"""
Dispute Demand Marts
====================

Overview
--------
Builds the analytical tables that describe how much of the bank's service demand is about
disputed transactions and how that demand is handled today: dispute cases per month, how they
are resolved, contact volumes and handling times per reason, and how customers rated the
contacts. The workflow analysis (``pipelines.analysis``) and any dashboard read these tables and
nothing else, so every published figure traces to one mart.

Scope
-----
In: reading the cleaned layer, defining what counts as a dispute case, writing the marts and a
manifest with the lineage of the run.
Out: rendering the report (``pipelines.analysis``) and cleaning the raw data (``pipelines.silver``).

Design Principles
-----------------
- The marts hold counts, sums and rates over groups, never a customer, account or free-text
  value, so they can be published and loaded into a dashboard as they are.
- A dispute case is a complaint of the category ``Transactions`` whose subcategory is
  ``Cargo no reconocido`` (an unrecognised charge). The source has no explicit dispute flag: this
  proxy, and the fact that complaints carry no link to the contact that originated them, are
  stated in the report.
- Every mart is ordered by its key, so the same cleaned layer and code produce byte-identical
  files and manifest.

Runtime Contract
----------------
``build_marts(silver_dir, gold_dir, *, code_version) -> MartManifest``
``read_mart(gold_dir, name) -> list[dict[str, Any]]``
``MART_NAMES``: the marts written, in order.

Limitations
-----------
The dispute proxy covers unrecognised charges only; other kinds of dispute (duplicates, wrong
amounts) cannot be told apart in the source. Contacts cannot be tied to the cases they produced.
"""

from __future__ import annotations

# Standard libraries
import hashlib  # Digests of the inputs and outputs recorded in the manifest
import json  # Manifest serialisation
import os  # Atomic replacement of files
import tempfile  # Working database that never outlives the build
from dataclasses import dataclass  # Immutable manifest object
from pathlib import Path  # Locations of the inputs and outputs
from typing import Any  # Query results

# Third-party libraries
import duckdb  # Analytical SQL over the cleaned Parquet files

# Local modules
from pipelines.raw import quote_literal  # Safe SQL string literals

# -----------------------------------------------------------------------------
# Definitions
# -----------------------------------------------------------------------------

# A dispute case is an unrecognised-charge complaint about a transaction.
DISPUTE_CATEGORY = "Transactions"
DISPUTE_SUBCATEGORY = "Cargo no reconocido"

# Cleaned tables the marts read.
SILVER_INPUTS = ("complaints", "call_center_interactions", "satisfaction_surveys")

# Sentiment labels of the source that express a negative experience.
NEGATIVE_SENTIMENTS = ("Negativo", "Muy Negativo")

# Statuses of a case that has reached an outcome.
CLOSED_STATUSES = ("Resolved", "Closed")

MANIFEST_NAME = "manifest.json"


def _dispute_filter() -> str:
    """SQL condition that selects dispute cases among the complaints."""
    return (
        f"category = {quote_literal(DISPUTE_CATEGORY)} "
        f"AND subcategory = {quote_literal(DISPUTE_SUBCATEGORY)}"
    )


def _in_list(values: tuple[str, ...]) -> str:
    """SQL list of string literals."""
    return ", ".join(quote_literal(value) for value in values)


# Each mart is a query over views named after the cleaned tables, ordered by its key.
_MARTS: dict[str, str] = {
    "dispute_cases_monthly": f"""
        SELECT
            CAST(date_trunc('month', creation_date) AS DATE) AS month,
            count(*) AS cases,
            count(*) FILTER (WHERE status IN ({_in_list(CLOSED_STATUSES)})) AS closed_cases,
            count(*) FILTER (WHERE status = 'Escalated') AS escalated_cases,
            count(*) FILTER (WHERE status = 'Rejected') AS rejected_cases,
            count(*) FILTER (WHERE sla_breached) AS sla_breached_cases,
            count(*) FILTER (WHERE is_repeat_complainer) AS repeat_complainer_cases,
            count(*) FILTER (WHERE first_response_date IS NOT NULL) AS first_response_cases
        FROM complaints
        WHERE {_dispute_filter()}
        GROUP BY 1
        ORDER BY 1
    """,
    "dispute_resolution": f"""
        SELECT
            status,
            count(*) AS cases,
            count(resolution_days) AS cases_with_days,
            sum(resolution_days) AS days_sum,
            quantile_cont(resolution_days, 0.5) AS median_days,
            quantile_cont(resolution_days, 0.9) AS p90_days,
            count(*) FILTER (WHERE sla_breached) AS sla_breached_cases
        FROM complaints
        WHERE {_dispute_filter()}
        GROUP BY 1
        ORDER BY 1
    """,
    "dispute_resolution_overall": f"""
        SELECT
            count(resolution_days) AS cases_with_days,
            quantile_cont(resolution_days, 0.5) AS median_days,
            quantile_cont(resolution_days, 0.9) AS p90_days
        FROM complaints
        WHERE {_dispute_filter()} AND status IN ({_in_list(CLOSED_STATUSES)})
    """,
    "dispute_claims_by_currency": f"""
        SELECT
            coalesce(currency, 'unknown') AS currency,
            count(*) AS cases,
            count(claimed_amount) AS cases_with_amount,
            sum(claimed_amount) AS claimed_total
        FROM complaints
        WHERE {_dispute_filter()}
        GROUP BY 1
        ORDER BY 1
    """,
    "complaint_category_mix": """
        SELECT
            category,
            coalesce(subcategory, 'unspecified') AS subcategory,
            count(*) AS cases
        FROM complaints
        GROUP BY 1, 2
        ORDER BY 1, 2
    """,
    "contact_demand_monthly": f"""
        SELECT
            CAST(date_trunc('month', interaction_date) AS DATE) AS month,
            reason_category,
            count(*) AS interactions,
            count(duration_seconds) AS interactions_with_duration,
            sum(duration_seconds) AS duration_seconds_sum,
            count(wait_time_seconds) AS interactions_with_wait,
            sum(wait_time_seconds) AS wait_seconds_sum,
            count(*) FILTER (WHERE was_resolved) AS resolved_interactions,
            count(*) FILTER (WHERE was_escalated) AS escalated_interactions,
            count(*) FILTER (WHERE requires_followup) AS followup_interactions,
            count(*) FILTER (WHERE detected_sentiment = 'Neutral') AS neutral_interactions,
            count(*) FILTER (WHERE detected_sentiment IN ({_in_list(NEGATIVE_SENTIMENTS)}))
                AS negative_interactions,
            count(sentiment_score) AS interactions_with_score,
            sum(sentiment_score) AS sentiment_score_sum
        FROM call_center_interactions
        GROUP BY 1, 2
        ORDER BY 1, 2
    """,
    "contact_satisfaction": """
        SELECT
            i.reason_category,
            count(*) AS surveys,
            count(s.main_score) AS surveys_with_score,
            sum(s.main_score) AS score_sum
        FROM satisfaction_surveys AS s
        JOIN call_center_interactions AS i ON i.interaction_id = s.interaction_id
        GROUP BY 1
        ORDER BY 1
    """,
}

MART_NAMES: tuple[str, ...] = tuple(_MARTS)


# -----------------------------------------------------------------------------
# Manifest
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MartManifest:
    """Lineage of one build: what it read, what it wrote, under which definitions."""

    code_version: str
    inputs: dict[str, str]
    marts: dict[str, dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        """The manifest as JSON-serialisable data."""
        return {
            "code_version": self.code_version,
            "definitions": {
                "dispute_category": DISPUTE_CATEGORY,
                "dispute_subcategory": DISPUTE_SUBCATEGORY,
            },
            "inputs": self.inputs,
            "marts": self.marts,
        }


def _sha256(path: Path) -> str:
    """SHA-256 of a file, read in one pass."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def _silver_path(silver_dir: Path, name: str) -> Path:
    """Location of a cleaned table."""
    return silver_dir / "silver" / f"{name}.parquet"


def build_marts(silver_dir: Path, gold_dir: Path, *, code_version: str) -> MartManifest:
    """Write every mart under ``gold_dir`` and return the manifest of the build.

    Parameters
    ----------
    silver_dir : Path
        Root of the cleaned layer written by ``pipelines.silver``.
    gold_dir : Path
        Directory that receives one Parquet file per mart and ``manifest.json``.
    code_version : str
        Version of the code, recorded in the manifest.

    Returns
    -------
    MartManifest
        Digests of the inputs, and row counts and digests of the outputs.

    Raises
    ------
    FileNotFoundError
        When a cleaned table the marts read is missing.
    """
    # Refuse to build from a partial cleaned layer
    inputs: dict[str, str] = {}
    for name in SILVER_INPUTS:
        path = _silver_path(silver_dir, name)
        if not path.is_file():
            raise FileNotFoundError(f"cleaned table {name} not found; run the cleaning stage first")
        inputs[name] = _sha256(path)

    gold_dir.mkdir(parents=True, exist_ok=True)
    marts: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory() as workdir:
        con = duckdb.connect(str(Path(workdir) / "gold.duckdb"))
        try:
            for name in SILVER_INPUTS:
                source = quote_literal(str(_silver_path(silver_dir, name)))
                con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet({source})")
            # Write each mart atomically, in a fixed order
            for name, query in _MARTS.items():
                target = gold_dir / f"{name}.parquet"
                temporary = target.with_suffix(".parquet.tmp")
                con.execute(f"COPY ({query}) TO {quote_literal(str(temporary))} (FORMAT PARQUET)")
                os.replace(temporary, target)
                (rows,) = con.execute(
                    f"SELECT count(*) FROM read_parquet({quote_literal(str(target))})"
                ).fetchone() or (0,)
                marts[name] = {"rows": rows, "sha256": _sha256(target)}
        finally:
            con.close()

    manifest = MartManifest(code_version=code_version, inputs=inputs, marts=marts)
    text = json.dumps(manifest.as_dict(), indent=2, sort_keys=True) + "\n"
    temporary_manifest = gold_dir / f"{MANIFEST_NAME}.tmp"
    temporary_manifest.write_text(text, encoding="utf-8")
    os.replace(temporary_manifest, gold_dir / MANIFEST_NAME)
    return manifest


def read_mart(gold_dir: Path, name: str) -> list[dict[str, Any]]:
    """Rows of one mart as dictionaries, in the order they were written.

    Raises
    ------
    KeyError
        When ``name`` is not a mart of this module.
    FileNotFoundError
        When the mart has not been built.
    """
    if name not in _MARTS:
        raise KeyError(f"unknown mart {name}")
    path = gold_dir / f"{name}.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"mart {name} not found; run the build first")
    con = duckdb.connect()
    try:
        cursor = con.execute(f"SELECT * FROM read_parquet({quote_literal(str(path))})")
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        con.close()
