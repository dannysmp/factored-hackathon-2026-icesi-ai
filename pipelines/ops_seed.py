"""
Operational Seed
================

Overview
--------
Builds the curated ~500-customer seed the running service reads from (ADR-9): a deterministic,
written stratification rule selects Active customers so that every situation the policy
distinguishes is present, then every selected customer's own products and transactions are
carried with them. It also writes ``reports/ops-seed.md``, stating plainly that the seed is
curated and comparing its mix and null rates with the full source's.

Scope
-----
In: reading the cleaned layer, the selection rule (``AC-E4-44``), masking the two contact fields
the serving store keeps, the command line ``python -m pipelines.ops_seed``.
Out: applying the migrations that create the serving-store tables (``app.persistence.migrate``),
loading the built seed into Postgres (``app.persistence.load_seed``), the ``cases`` table (see
Limitations).

Design Principles
------------------
- **Written before the seed, deterministic.** Selection ranks every Active customer by a stable
  hash of their identifier (fixed seed, never ``random()``), then takes the lowest-ranked
  customers that satisfy each stratum before padding up to the target with the next-ranked
  customers; the same cleaned layer and this same rule always choose the same customers.
  ``TARGET_CUSTOMERS`` is a floor the padding step brings the selection up to, not a ceiling: the
  stratum and segment-country guarantees run first and are never trimmed back down to fit it, so
  the final count can exceed it when coverage demands more customers than the target alone would
  hold (about 500 in practice, per AC-E4-44's own wording; coverage is what AC-E4-44 requires,
  not an exact count).
- **One amount rule.** ``amount_usd`` and its provenance are computed with
  ``pipelines.amounts``, the same module the risk-feature mart uses, so a transaction shared by
  both never gets two answers.
- **Repeat complainer is a point-in-time fact** (ADR-15): a customer carries the flag only when
  their latest complaint filed on or before the reference date carries it; a later complaint is
  never consulted.
- **No PII beyond what the store is allowed to hold.** Document number, birth date, address and
  full email or phone are never read into the seed; email and phone are masked before they reach
  a Parquet file, not after (``AC-E4-46``).
- **Active only** (``AC-E4-48``): the selection reads only customers whose ``customer_status`` is
  ``Active``; a suspended, blocked or closed customer or product never appears, by construction.

Runtime Contract
-----------------
``build_seed(silver_dir, gold_dir, *, code_version) -> SeedManifest``
``render_report(manifest) -> str``

Limitations
-----------
The seed populates ``customers``, ``products`` and ``transactions`` only. ``cases`` starts empty:
a case's own fields (session identifier, idempotency key, the policy version and reason code as
this system computes them) have no faithful historical analog in a legacy complaint export, so a
case is only ever created live, through the case-service tool. "A customer with an open case" is
a selection criterion read from ``complaints.status``, not a synthesized row. Because the seed is
curated, rates measured on it (merchant-null share, currency mix, and so on) describe the seed,
never the population; the report states this and shows the source's own rates beside it.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command line
import hashlib  # Digests of inputs and outputs
import json  # Manifest serialisation
import logging  # Progress events, never print
import os  # Atomic replacement of files
import tempfile  # Working database that never outlives the build
from collections.abc import Callable, Sequence  # Argument type of main, the mask signature
from dataclasses import dataclass  # Immutable manifest object
from pathlib import Path  # Locations of the inputs and outputs
from typing import Any  # Manifest and query-row contents

# Third-party libraries
import duckdb  # Window functions and Python UDFs over the cleaned Parquet files

# Local modules
from pipelines.amounts import usd_amount_expr, usd_amount_provenance_expr  # One USD-amount rule
from pipelines.raw import quote_literal  # Safe SQL string literals
from pipelines.silver import git_version  # Same code-version rule as the cleaning stage

logger = logging.getLogger(__name__)

SILVER_INPUTS = ("customers", "products", "transactions", "complaints", "daily_exchange_rates")

# The written selection rule (AC-E4-44). Changing any of these constants changes the seed and
# needs a new checksum in the test that pins it.
RANK_SEED = "20260618-ops-seed"  # Arbitrary, fixed forever; part of the rule, not the data.
TARGET_CUSTOMERS = 500
MIN_PER_STRATUM = 5
DAY_TOLERANCE = 2
REFERENCE_OFFSETS_DAYS = (60, 90, 120)
NEAR_TRANSFER_USD = 5000.0
NEAR_TRANSFER_TOLERANCE_USD = 250.0
SEGMENTS = ("Basic", "Plus", "Premium", "Student")
COUNTRIES = ("México", "Colombia", "Argentina")

# One flag per AC-E4-44 criterion that is a property of a transaction or a complaint; segment and
# country coverage is handled separately, over the customer's own two fields.
STRATUM_FLAGS: tuple[str, ...] = (
    "is_repeat_complainer",
    "has_open_case",
    "has_unconvertible_amount",
    "has_near_5000_transfer",
    "has_declined",
    "has_merchant_null",
    "has_category_null",
    *[f"has_tx_{offset}d_before" for offset in REFERENCE_OFFSETS_DAYS],
)

CUSTOMERS_NAME = "customers.parquet"
PRODUCTS_NAME = "products.parquet"
TRANSACTIONS_NAME = "transactions.parquet"
MANIFEST_NAME = "manifest.json"


def _sha256(path: Path) -> str:
    """SHA-256 of a file, read in one pass."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


# -----------------------------------------------------------------------------
# Masking (AC-E4-46): applied before a row ever reaches a Parquet file
# -----------------------------------------------------------------------------


def _mask_email(email: str | None) -> str | None:
    """The first character of the local part, four asterisks, then the domain; never the full
    address."""
    if email is None:
        return None
    local, _, domain = email.partition("@")
    if not domain:
        return "****"
    head = local[:1]
    return f"{head}****@{domain}"


def _mask_phone(phone: str | None) -> str | None:
    """Four asterisks, then the last two digits; never the full number."""
    if phone is None:
        return None
    digits = "".join(char for char in phone if char.isdigit())
    tail = digits[-2:]
    return f"****{tail}"


def _register_masks(con: duckdb.DuckDBPyConnection) -> None:
    """Expose the masking functions to SQL, so a masked column never round-trips through Python."""
    con.create_function("mask_email", _mask_email, ["VARCHAR"], "VARCHAR")
    con.create_function("mask_phone", _mask_phone, ["VARCHAR"], "VARCHAR")


# -----------------------------------------------------------------------------
# Selection (AC-E4-44)
# -----------------------------------------------------------------------------


def _flags_query(reference_date: str) -> str:
    """One row per Active customer: every AC-E4-44 stratum flag and a deterministic rank key."""
    ref = quote_literal(reference_date)
    usd_expr = usd_amount_expr(
        amount="t.amount",
        currency="t.currency",
        amount_usd="t.amount_usd",
        exchange_rate="r.exchange_rate",
    )
    near_low = NEAR_TRANSFER_USD - NEAR_TRANSFER_TOLERANCE_USD
    near_high = NEAR_TRANSFER_USD + NEAR_TRANSFER_TOLERANCE_USD
    window_flags = ",\n            ".join(
        f"bool_or(tx_date BETWEEN DATE {ref} - INTERVAL {offset + DAY_TOLERANCE} DAY "
        f"AND DATE {ref} - INTERVAL {offset - DAY_TOLERANCE} DAY) AS has_tx_{offset}d_before"
        for offset in REFERENCE_OFFSETS_DAYS
    )
    selected_flags = ",\n        ".join(
        f"coalesce(f.has_tx_{offset}d_before, false) AS has_tx_{offset}d_before"
        for offset in REFERENCE_OFFSETS_DAYS
    )
    return f"""
    WITH rates AS (
        SELECT date, source_currency, exchange_rate
        FROM daily_exchange_rates WHERE target_currency = 'USD'
    ),
    resolved AS (
        SELECT
            t.customer_id,
            t.transaction_type,
            t.transaction_status,
            t.merchant_name,
            t.merchant_category,
            CAST(t.transaction_date AS DATE) AS tx_date,
            {usd_expr} AS amount_usd
        FROM transactions AS t
        LEFT JOIN rates AS r
            ON r.date = CAST(t.transaction_date AS DATE) AND r.source_currency = t.currency
    ),
    complaint_rank AS (
        SELECT customer_id, is_repeat_complainer,
               row_number() OVER (PARTITION BY customer_id ORDER BY creation_date DESC) AS rn
        FROM complaints
        WHERE CAST(creation_date AS DATE) <= DATE {ref}
    ),
    repeat_complainers AS (
        SELECT customer_id FROM complaint_rank WHERE rn = 1 AND is_repeat_complainer
    ),
    open_case_customers AS (
        SELECT DISTINCT customer_id FROM complaints
        WHERE status IN ('Open', 'Escalated', 'In Process')
    ),
    tx_flags AS (
        SELECT
            customer_id,
            bool_or(amount_usd IS NULL) AS has_unconvertible_amount,
            bool_or(
                transaction_type = 'Transfer' AND amount_usd BETWEEN {near_low} AND {near_high}
            ) AS has_near_5000_transfer,
            bool_or(transaction_status = 'Declined') AS has_declined,
            bool_or(merchant_name IS NULL) AS has_merchant_null,
            bool_or(merchant_category IS NULL) AS has_category_null,
            {window_flags}
        FROM resolved
        GROUP BY customer_id
    )
    SELECT
        c.customer_id,
        c.segment,
        c.country,
        coalesce(rc.customer_id IS NOT NULL, false) AS is_repeat_complainer,
        coalesce(oc.customer_id IS NOT NULL, false) AS has_open_case,
        coalesce(f.has_unconvertible_amount, false) AS has_unconvertible_amount,
        coalesce(f.has_near_5000_transfer, false) AS has_near_5000_transfer,
        coalesce(f.has_declined, false) AS has_declined,
        coalesce(f.has_merchant_null, false) AS has_merchant_null,
        coalesce(f.has_category_null, false) AS has_category_null,
        {selected_flags},
        hash(c.customer_id || {quote_literal(RANK_SEED)}) AS rank_key
    FROM customers AS c
    LEFT JOIN repeat_complainers AS rc ON rc.customer_id = c.customer_id
    LEFT JOIN open_case_customers AS oc ON oc.customer_id = c.customer_id
    LEFT JOIN tx_flags AS f ON f.customer_id = c.customer_id
    WHERE c.customer_status = 'Active'
    """


def _select_customers(
    con: duckdb.DuckDBPyConnection, reference_date: str
) -> tuple[tuple[str, ...], dict[str, int]]:
    """The stratified customer selection: deterministic, covers every AC-E4-44 criterion.

    Ranks every Active customer by a stable hash of their identifier, then takes the
    lowest-ranked customers that satisfy each stratum (at least ``MIN_PER_STRATUM``, or every
    one there is when fewer exist) and each segment-country pair, before padding up to
    ``TARGET_CUSTOMERS`` with the next-ranked customers overall. The stratum and segment-country
    guarantees are never trimmed back to fit the target: it is a floor the padding step reaches,
    not a ceiling the guarantees are capped by, so the final count can exceed it.

    Returns
    -------
    tuple[tuple[str, ...], dict[str, int]]
        The selected customer identifiers, sorted; how many selected customers carry each
        stratum flag or segment-country pair, for the report.
    """
    cursor = con.execute(_flags_query(reference_date))
    columns = [column[0] for column in cursor.description]
    rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    rows.sort(key=lambda row: (row["rank_key"], row["customer_id"]))

    selected: dict[str, dict[str, Any]] = {}

    def _take(predicate: Callable[[dict[str, Any]], bool], count: int) -> None:
        taken = 0
        for row in rows:
            if row["customer_id"] in selected:
                continue
            if predicate(row):
                selected[row["customer_id"]] = row
                taken += 1
                if taken >= count:
                    return

    for flag in STRATUM_FLAGS:
        _take(lambda row: bool(row[flag]), MIN_PER_STRATUM)  # noqa: B023 (called this iteration)
    for segment in SEGMENTS:
        for country in COUNTRIES:
            _take(
                lambda row: row["segment"] == segment and row["country"] == country,  # noqa: B023
                MIN_PER_STRATUM,
            )
    for row in rows:
        if len(selected) >= TARGET_CUSTOMERS:
            break
        selected.setdefault(row["customer_id"], row)

    coverage = {flag: sum(1 for row in selected.values() if row[flag]) for flag in STRATUM_FLAGS}
    coverage.update(
        {
            f"segment={segment},country={country}": sum(
                1
                for row in selected.values()
                if row["segment"] == segment and row["country"] == country
            )
            for segment in SEGMENTS
            for country in COUNTRIES
        }
    )
    return tuple(sorted(selected)), coverage


# -----------------------------------------------------------------------------
# Output queries (must match app/persistence/migrations/0001_serving_store.sql exactly)
# -----------------------------------------------------------------------------


def _customers_query(reference_date: str) -> str:
    """One row per chosen customer, including their point-in-time repeat-complainer flag
    (ADR-15): the same ``complaints`` lookup ``_flags_query`` uses to select candidates, re-run
    here since selection and output are independent queries and a candidate's flag is not
    otherwise carried between them."""
    ref = quote_literal(reference_date)
    return f"""
    WITH complaint_rank AS (
        SELECT customer_id, is_repeat_complainer,
               row_number() OVER (PARTITION BY customer_id ORDER BY creation_date DESC) AS rn
        FROM complaints
        WHERE CAST(creation_date AS DATE) <= DATE {ref}
    ),
    repeat_complainers AS (
        SELECT customer_id FROM complaint_rank WHERE rn = 1 AND is_repeat_complainer
    )
    SELECT
        c.customer_id,
        c.first_name,
        c.last_name,
        mask_email(c.email) AS masked_email,
        mask_phone(coalesce(c.mobile_phone, c.landline_phone)) AS masked_phone,
        c.country,
        c.customer_status,
        coalesce(rc.customer_id IS NOT NULL, false) AS is_repeat_complainer
    FROM customers AS c
    JOIN chosen_customers AS s ON s.customer_id = c.customer_id
    LEFT JOIN repeat_complainers AS rc ON rc.customer_id = c.customer_id
    ORDER BY c.customer_id
    """


_PRODUCTS_QUERY = """
    SELECT
        p.product_id,
        p.customer_id,
        p.product_type,
        right(p.product_number, 4) AS last4,
        p.product_status
    FROM products AS p
    JOIN chosen_customers AS s ON s.customer_id = p.customer_id
    ORDER BY p.product_id
"""


def _transactions_query() -> str:
    usd_expr = usd_amount_expr(
        amount="t.amount",
        currency="t.currency",
        amount_usd="t.amount_usd",
        exchange_rate="r.exchange_rate",
    )
    provenance_expr = usd_amount_provenance_expr(
        reported_usd="reported_usd", currency="currency", computed_usd="amount_usd"
    )
    return f"""
    WITH rates AS (
        SELECT date, source_currency, exchange_rate
        FROM daily_exchange_rates WHERE target_currency = 'USD'
    ),
    resolved AS (
        SELECT
            t.transaction_id,
            t.customer_id,
            t.product_id,
            t.transaction_date,
            t.transaction_type,
            t.merchant_name,
            t.amount,
            t.currency,
            t.amount_usd AS reported_usd,
            {usd_expr} AS amount_usd,
            t.transaction_status
        FROM transactions AS t
        JOIN chosen_customers AS s ON s.customer_id = t.customer_id
        LEFT JOIN rates AS r
            ON r.date = CAST(t.transaction_date AS DATE) AND r.source_currency = t.currency
    )
    SELECT
        transaction_id, customer_id, product_id, transaction_date, transaction_type,
        merchant_name, amount, currency, amount_usd,
        {provenance_expr} AS amount_usd_provenance,
        transaction_status
    FROM resolved
    ORDER BY transaction_id
    """


# -----------------------------------------------------------------------------
# Manifest
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeedManifest:
    """What the build read, selected and wrote."""

    code_version: str
    inputs: dict[str, str]
    reference_date: str
    selected_customers: int
    coverage: dict[str, int]
    rows: dict[str, int]
    mix: dict[str, Any]
    output_sha256: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        """The manifest as JSON-serialisable data."""
        return {
            "code_version": self.code_version,
            "rule": {
                "target_customers": TARGET_CUSTOMERS,
                "min_per_stratum": MIN_PER_STRATUM,
                "rank_seed": RANK_SEED,
            },
            "inputs": self.inputs,
            "reference_date": self.reference_date,
            "selected_customers": self.selected_customers,
            "coverage": self.coverage,
            "rows": self.rows,
            "mix": self.mix,
            "output_sha256": self.output_sha256,
        }


def _source_provenance_query() -> str:
    """The same two-step amount rule (``pipelines.amounts``) over the full source, unfiltered.

    The cleaned ``transactions`` table carries no ``amount_usd_provenance`` column of its own
    (only the seed's own output does, once written); this computes it fresh, so the seed's
    provenance mix can be measured beside the source's without re-deriving the rule twice.
    """
    usd_expr = usd_amount_expr(
        amount="t.amount",
        currency="t.currency",
        amount_usd="t.amount_usd",
        exchange_rate="r.exchange_rate",
    )
    provenance_expr = usd_amount_provenance_expr(
        reported_usd="reported_usd", currency="currency", computed_usd="amount_usd"
    )
    return f"""
    WITH rates AS (
        SELECT date, source_currency, exchange_rate
        FROM daily_exchange_rates WHERE target_currency = 'USD'
    ),
    enriched AS (
        SELECT
            t.merchant_name, t.transaction_status, t.currency,
            t.amount_usd AS reported_usd,
            {usd_expr} AS amount_usd
        FROM transactions AS t
        LEFT JOIN rates AS r
            ON r.date = CAST(t.transaction_date AS DATE) AND r.source_currency = t.currency
    )
    SELECT merchant_name, transaction_status, {provenance_expr} AS amount_usd_provenance
    FROM enriched
    """


def _measure_mix(con: duckdb.DuckDBPyConnection, gold_dir: Path) -> dict[str, Any]:
    """Null and mix rates of the seed's transactions beside the full source's (AC-E4-45).

    Segment and country mix are not measured here: the serving store keeps no ``segment``
    column (migration 0001 only mirrors what a tool may read back), so the seed's own segment and
    country mix is read from the selection's coverage tally instead (``render_report``); this
    function reports only the source's segment and country mix, for the same comparison.
    """
    seed_transactions = f"read_parquet({quote_literal(str(gold_dir / TRANSACTIONS_NAME))})"
    source_transactions = f"({_source_provenance_query()})"

    def _rate(table: str, condition: str) -> float:
        (total, hits) = con.execute(
            f"SELECT count(*), count(*) FILTER (WHERE {condition}) FROM {table}"
        ).fetchone() or (0, 0)
        return hits / total if total else 0.0

    def _compare_tx(condition: str) -> dict[str, float]:
        return {
            "seed": _rate(seed_transactions, condition),
            "source": _rate(source_transactions, condition),
        }

    return {
        "merchant_null_rate": _compare_tx("merchant_name IS NULL"),
        "declined_rate": _compare_tx("transaction_status = 'Declined'"),
        "amount_unknown_rate": _compare_tx("amount_usd_provenance = 'unknown'"),
        "amount_converted_rate": _compare_tx("amount_usd_provenance = 'converted'"),
        "source_segment_mix": {
            segment: _rate("customers", f"segment = {quote_literal(segment)}")
            for segment in SEGMENTS
        },
        "source_country_mix": {
            country: _rate("customers", f"country = {quote_literal(country)}")
            for country in COUNTRIES
        },
    }


# -----------------------------------------------------------------------------
# Build
# -----------------------------------------------------------------------------


def build_seed(silver_dir: Path, gold_dir: Path, *, code_version: str) -> SeedManifest:
    """Select the customers, write the three seed tables and the report's manifest.

    Raises
    ------
    FileNotFoundError
        When a cleaned table the seed reads is missing.
    ValueError
        When the cleaned ``transactions`` table is empty (no reference date to seed from).
    """
    inputs: dict[str, str] = {}
    for name in SILVER_INPUTS:
        path = silver_dir / "silver" / f"{name}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"cleaned table {name} not found; run the cleaning stage first")
        inputs[name] = _sha256(path)

    gold_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as workdir:
        con = duckdb.connect(str(Path(workdir) / "ops_seed.duckdb"))
        try:
            con.execute(f"PRAGMA temp_directory={quote_literal(workdir)}")
            for name in SILVER_INPUTS:
                source = quote_literal(str(silver_dir / "silver" / f"{name}.parquet"))
                con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet({source})")
            _register_masks(con)

            (reference_date,) = con.execute(
                "SELECT CAST(max(transaction_date) AS DATE) FROM transactions"
            ).fetchone() or (None,)
            if reference_date is None:
                raise ValueError("transactions is empty; no reference date to seed from")
            reference_date_text = reference_date.isoformat()

            chosen_ids, coverage = _select_customers(con, reference_date_text)
            con.execute("CREATE TEMP TABLE chosen_customers (customer_id VARCHAR PRIMARY KEY)")
            con.executemany(
                "INSERT INTO chosen_customers VALUES (?)", [(cid,) for cid in chosen_ids]
            )

            rows: dict[str, int] = {}
            digests: dict[str, str] = {}
            for name, query in (
                (CUSTOMERS_NAME, _customers_query(reference_date_text)),
                (PRODUCTS_NAME, _PRODUCTS_QUERY),
                (TRANSACTIONS_NAME, _transactions_query()),
            ):
                target = gold_dir / name
                temporary = target.with_suffix(".parquet.tmp")
                con.execute(f"COPY ({query}) TO {quote_literal(str(temporary))} (FORMAT PARQUET)")
                os.replace(temporary, target)
                (count,) = con.execute(
                    f"SELECT count(*) FROM read_parquet({quote_literal(str(target))})"
                ).fetchone() or (0,)
                rows[name] = int(count)
                digests[name] = _sha256(target)

            mix = _measure_mix(con, gold_dir)
        finally:
            con.close()

    manifest = SeedManifest(
        code_version=code_version,
        inputs=inputs,
        reference_date=reference_date_text,
        selected_customers=len(chosen_ids),
        coverage=coverage,
        rows=rows,
        mix=mix,
        output_sha256=digests,
    )
    text = json.dumps(manifest.as_dict(), indent=2, sort_keys=True) + "\n"
    manifest_temporary = gold_dir / f"{MANIFEST_NAME}.tmp"
    manifest_temporary.write_text(text, encoding="utf-8")
    os.replace(manifest_temporary, gold_dir / MANIFEST_NAME)
    return manifest


# -----------------------------------------------------------------------------
# Report
# -----------------------------------------------------------------------------


def _table(headers: list[str], body: list[list[str]]) -> str:
    """Markdown table."""
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _pct(value: float) -> str:
    return f"{value * 100:.2f} %"


def render_report(manifest: SeedManifest) -> str:
    """Render ``reports/ops-seed.md`` from the manifest (a pure function of it)."""
    coverage_rows = [[f"`{flag}`", str(manifest.coverage.get(flag, 0))] for flag in STRATUM_FLAGS]
    segment_country_rows = [
        [segment, country, str(manifest.coverage.get(f"segment={segment},country={country}", 0))]
        for segment in SEGMENTS
        for country in COUNTRIES
    ]
    mix = manifest.mix
    mix_rows = [
        [label, _pct(mix[key]["seed"]), _pct(mix[key]["source"])]
        for label, key in (
            ("Merchant name null", "merchant_null_rate"),
            ("Declined", "declined_rate"),
            ("Amount unknown", "amount_unknown_rate"),
            ("Amount converted", "amount_converted_rate"),
        )
    ]
    n = manifest.selected_customers
    seed_segment_share = {
        segment: sum(
            manifest.coverage.get(f"segment={segment},country={country}", 0)
            for country in COUNTRIES
        )
        / n
        if n
        else 0.0
        for segment in SEGMENTS
    }
    seed_country_share = {
        country: sum(
            manifest.coverage.get(f"segment={segment},country={country}", 0) for segment in SEGMENTS
        )
        / n
        if n
        else 0.0
        for country in COUNTRIES
    }
    segment_mix_rows = [
        [segment, _pct(seed_segment_share[segment]), _pct(mix["source_segment_mix"][segment])]
        for segment in SEGMENTS
    ]
    country_mix_rows = [
        [country, _pct(seed_country_share[country]), _pct(mix["source_country_mix"][country])]
        for country in COUNTRIES
    ]
    lines = [
        "# Operational seed",
        "",
        "**This seed is curated, not a random sample.** It is built by a written, deterministic "
        "rule (`pipelines.ops_seed`) so that every situation the policy distinguishes is present, "
        "even though most of those situations are rare in the source. A rate measured on this "
        "seed describes the seed, never the population; every rate below is shown beside the "
        "same rate measured on the full cleaned layer.",
        "",
        f"Reference date `{manifest.reference_date}` (the newest transaction instant in the "
        "seed, read by the running service as `ops_meta.data_as_of`, ADR-15).",
        "",
        f"{manifest.selected_customers:,} customers selected, all `Active` (AC-E4-48). "
        f"{manifest.rows.get(PRODUCTS_NAME, 0):,} products, "
        f"{manifest.rows.get(TRANSACTIONS_NAME, 0):,} transactions.",
        "",
        "## 1. Coverage of the selection rule",
        "",
        "How many selected customers carry each stratum; the rule guarantees at least "
        f"{MIN_PER_STRATUM} wherever the source has that many.",
        "",
        _table(["Stratum", "Selected customers"], coverage_rows),
        "",
        _table(["Segment", "Country", "Selected customers"], segment_country_rows),
        "",
        "## 2. Null and mix rates: seed beside source",
        "",
        _table(["Rate", "Seed", "Source"], mix_rows),
        "",
        _table(["Segment", "Seed", "Source"], segment_mix_rows),
        "",
        _table(["Country", "Seed", "Source"], country_mix_rows),
        "",
        "## 3. What the seed does not contain",
        "",
        "No document number, birth date, address, full email or full phone (AC-E4-46): the seed "
        "never reads those source columns, and the two contact fields it keeps are masked before "
        "they reach a Parquet file. `cases` starts empty; see the module's Limitations.",
        "",
        "## 4. Lineage",
        "",
        _table(
            ["Cleaned table", "SHA-256"],
            [[name, digest[:12]] for name, digest in sorted(manifest.inputs.items())],
        ),
        "",
        _table(
            ["Output", "Rows", "SHA-256"],
            [
                [name, f"{manifest.rows.get(name, 0):,}", digest[:12]]
                for name, digest in sorted(manifest.output_sha256.items())
            ],
        ),
        "",
        f"Built by `{manifest.code_version}`.",
    ]
    return "\n".join(lines) + "\n"


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Build the seed and write the report; return the exit code."""
    parser = argparse.ArgumentParser(description="Build the operational seed and its report.")
    parser.add_argument("--silver", type=Path, default=Path("data/silver"))
    parser.add_argument("--gold", type=Path, default=Path("data/gold/ops_seed"))
    parser.add_argument("--report", type=Path, default=Path("reports/ops-seed.md"))
    parser.add_argument("--code-version", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        manifest = build_seed(
            args.silver, args.gold, code_version=args.code_version or git_version()
        )
    except (FileNotFoundError, ValueError, OSError, duckdb.Error) as error:
        logger.error("ops_seed_failed reason=%s", type(error).__name__)
        return 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(".md.tmp")
    temporary.write_text(render_report(manifest), encoding="utf-8")
    os.replace(temporary, args.report)
    logger.info("ops_seed_written customers=%d report=%s", manifest.selected_customers, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
