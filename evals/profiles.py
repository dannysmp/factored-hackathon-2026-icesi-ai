"""
Case Customer Profiles
=======================

Overview
--------
Finds the country and customer segment of the customer each golden case runs as, so
`evals.fairness` can slice results by them. A case names its customer through its ``seed_ref``;
the country is read from the serving store the system itself reads, and the segment from the
cleaned customers table of the data pipeline, which is the only place segment exists.

Scope
-----
In: ``load_case_profiles``, ``country_code`` and ``read_segments``.
Out: slicing and flagging (`evals.fairness`) and rendering (`evals.report`).

Design Principles
-----------------
- **Only the two fields this needs are read.** The cleaned customers table also holds identity
  documents and contact details; the query selects ``customer_id`` and ``segment`` and nothing
  else, and nothing here keeps a customer id past the lookup.
- **Missing is a value, not a failure.** A case whose customer cannot be resolved, or a cleaned
  table that is not present, yields ``None`` for the field; `evals.fairness` reports those cases
  in an ``unknown`` slice instead of dropping them or aborting the evaluation.

Runtime Contract
----------------
``load_case_profiles(dsn, cases, *, silver_dir) -> dict[case_id, CaseProfile]``: one store
connection and one Parquet read per call.

Limitations
-----------
A frozen adversarial-bank customer is not in the cleaned customers table, so its segment is
unknown. The segment is read from the data on disk at report time, not from what the serving store
held during the run.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Collection, Sequence  # Parameter types
from pathlib import Path  # The pipeline's output location

# Third-party libraries
import duckdb  # Reads the cleaned customers table
import psycopg  # Reads the serving store

# Local modules
from evals.fairness import CaseProfile  # The profile record
from evals.models import Case  # The golden case: seed_ref
from evals.runner.seed_resolution import resolve_customer_id  # seed_ref -> customer id

_COUNTRY_CODES = {"México": "MX", "Colombia": "CO", "Argentina": "AR"}


def country_code(country: str) -> str:
    """The two-letter code of a stored country name; an unrecognised name is kept as stored."""
    return _COUNTRY_CODES.get(country, country)


def read_segments(silver_dir: Path, customer_ids: Collection[str]) -> dict[str, str]:
    """The segment of each given customer in the cleaned customers table.

    An absent table, or a customer absent from it, yields no entry.
    """
    path = silver_dir / "silver" / "customers.parquet"
    if not path.is_file() or not customer_ids:
        return {}
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            "SELECT customer_id, segment FROM read_parquet(?) WHERE list_contains(?, customer_id)",
            [str(path), sorted(set(customer_ids))],
        ).fetchall()
    finally:
        connection.close()
    return {customer_id: segment for customer_id, segment in rows if segment}


def _read_countries(dsn: str, customer_ids: Sequence[str]) -> dict[str, str]:
    if not customer_ids:
        return {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT customer_id, country FROM customers WHERE customer_id = ANY(%s)",
            (list(customer_ids),),
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def load_case_profiles(
    dsn: str, cases: Sequence[Case], *, silver_dir: Path
) -> dict[str, CaseProfile]:
    """The country and segment of the customer each case runs as, by case id.

    A case whose ``seed_ref`` resolves to no customer has no entry.
    """
    customer_of: dict[str, str] = {}
    for case in cases:
        try:
            customer_of[case.case_id] = resolve_customer_id(dsn, case.seed_ref)
        except ValueError:
            continue
    customer_ids = sorted(set(customer_of.values()))
    countries = _read_countries(dsn, customer_ids)
    segments = read_segments(silver_dir, customer_ids)
    return {
        case_id: CaseProfile(
            country=country_code(countries[customer_id]) if customer_id in countries else None,
            segment=segments.get(customer_id),
        )
        for case_id, customer_id in customer_of.items()
    }
