"""
Demo Persona Selection Query
==============================

Overview
--------
Selects five real, Active customers from the operational seed (``data/gold/ops_seed``) for
``personas/demo_personas_v1.yaml``, one per demo scenario: an eligible dispute in Spanish,
Portuguese and English, an amount-above-threshold escalation in Spanish, and a repeat complainer
in Portuguese. Prints the resulting slug-to-``customer_id`` mapping; the persona file's own
``customer_id`` values are then set by hand from this output, since a synthetic seed identifier
belongs directly in a reviewed file, not computed at load time.

Scope
-----
In: the query and the print-out. Out: writing ``personas/demo_personas_v1.yaml`` (a manual,
reviewed edit); the start-up validation that later confirms these ids resolve
(``app.security.demo_personas``, unaffected by this script).

Design Principles
-----------------
- **Reuses the seed's own repeat-complainer determination, never re-derives it.**
  ``pipelines.ops_seed`` already establishes the rule — a customer's *latest* complaint on or
  before the reference date, keyed by that complaint's own ``is_repeat_complainer`` flag — when it
  decides which customers the seed carries at all; this script applies the identical rule (latest
  complaint, same cutoff) rather than writing a second, possibly-diverging version of it.
- **Every candidate's real outcome is confirmed through the engine, not assumed from the SQL
  alone.** ``app.domain.policy.engine.evaluate_dispute`` is the one function that decides
  eligibility and routing in the running service; this script calls it directly on each selected
  transaction with the reference date the demo deployment actually uses
  (``DATA_AS_OF_DATE``'s own default, ``docker-compose.prod.yml``), so a candidate is only
  reported if the real engine agrees with the SQL's own filter, not only because the filter looked
  right.
- **Deterministic selection.** Every candidate set is ordered by ``customer_id`` and the first row
  is taken, so re-running this script against the same seed always picks the same five customers;
  the five are also cross-checked for no repeated ``customer_id`` across scenarios.

Runtime Contract
-----------------
``python -m scripts.select_demo_personas`` prints the slug-to-``customer_id`` mapping and exits 0,
or raises if fewer than the needed candidates exist for any scenario.
"""

from __future__ import annotations

# Standard libraries
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

# Third-party libraries
import duckdb

# Local modules
from app.domain.policy import (
    DisputeCategory,
    DisputeRequest,
    Outcome,
    TransactionStatus,
    evaluate_dispute,
    load_policy,
)

logger = logging.getLogger(__name__)

#: DATA_AS_OF_DATE's own default for a demo deployment (docker-compose.prod.yml), matching the
#: golden set's own reference date so a candidate eligible today stays eligible in the demo.
REFERENCE_DATE = date(2026, 6, 18)

GOLD_DIR = Path("data/gold/ops_seed")
COMPLAINTS_PARQUET = Path("data/silver/silver/complaints.parquet")

#: ana, joao and emma — one eligible customer each.
_ELIGIBLE_PERSONAS_NEEDED = 3

#: customer_id, transaction_id, transaction_date, transaction_type, transaction_status,
#: amount_usd, product_type, is_repeat_complainer — one candidate transaction.
CandidateRow = tuple[str, str, date, str, str, Decimal, str, bool]

_CANDIDATES_SQL = """
WITH complaint_rank AS (
    SELECT customer_id, is_repeat_complainer,
           row_number() OVER (PARTITION BY customer_id ORDER BY creation_date DESC) AS rn
    FROM read_parquet(?)
    WHERE CAST(creation_date AS DATE) <= ?
),
repeat_complainers AS (
    SELECT customer_id FROM complaint_rank WHERE rn = 1 AND is_repeat_complainer
)
SELECT
    c.customer_id,
    t.transaction_id,
    CAST(t.transaction_date AS DATE) AS transaction_date,
    t.transaction_type,
    t.transaction_status,
    t.amount_usd,
    p.product_type,
    coalesce(rc.customer_id IS NOT NULL, false) AS is_repeat_complainer
FROM read_parquet(?) AS c
JOIN read_parquet(?) AS t ON t.customer_id = c.customer_id
JOIN read_parquet(?) AS p ON p.product_id = t.product_id
LEFT JOIN repeat_complainers AS rc ON rc.customer_id = c.customer_id
WHERE c.customer_status = 'Active'
  AND p.product_status = 'Active'
  AND t.transaction_type IN ('Purchase', 'Withdrawal', 'Transfer', 'Payment')
  AND p.product_type IN ('Cuenta Ahorro', 'Cuenta Corriente', 'Tarjeta Crédito', 'Tarjeta Débito')
  AND t.transaction_status = 'Approved'
  AND t.amount_usd IS NOT NULL
  AND CAST(t.transaction_date AS DATE) <= ?
  AND CAST(t.transaction_date AS DATE) >= ? - INTERVAL 120 DAY
ORDER BY c.customer_id, t.transaction_id
"""


def _candidates() -> list[CandidateRow]:
    """Every Active-customer, Active-product, in-window candidate transaction, ordered
    deterministically."""
    con = duckdb.connect()
    return con.execute(
        _CANDIDATES_SQL,
        [
            str(COMPLAINTS_PARQUET),
            REFERENCE_DATE,
            str(GOLD_DIR / "customers.parquet"),
            str(GOLD_DIR / "transactions.parquet"),
            str(GOLD_DIR / "products.parquet"),
            REFERENCE_DATE,
            REFERENCE_DATE,
        ],
    ).fetchall()


def _confirms(row: CandidateRow, *, expect: Outcome) -> bool:
    """Whether the real engine agrees this row's transaction produces ``expect``."""
    (
        _customer_id,
        transaction_id,
        transaction_date,
        transaction_type,
        transaction_status,
        amount_usd,
        product_type,
        is_repeat_complainer,
    ) = row
    request = DisputeRequest(
        transaction_ref=transaction_id,
        category=DisputeCategory.UNRECOGNIZED_CHARGE,
        transaction_date=transaction_date,
        transaction_status=TransactionStatus(transaction_status),
        transaction_type=transaction_type,
        product_type=product_type,
        amount_usd=amount_usd,
        nlu_confidence=1.0,
        is_repeat_complainer=is_repeat_complainer,
        has_open_case_for_transaction=False,
    )
    decision = evaluate_dispute(request, load_policy(), today=REFERENCE_DATE)
    return decision.outcome is expect


def select_personas(rows: list[CandidateRow] | None = None) -> dict[str, str]:
    """The slug-to-``customer_id`` mapping for the five demo customer personas.

    Parameters
    ----------
    rows
        Candidate rows, in ``_candidates``'s own column order; defaults to the real query against
        ``data/gold/ops_seed`` and ``data/silver/silver/complaints.parquet``. Overriding this is
        for tests only, so the selection and verification logic is checkable without the real
        seed files present.

    Raises
    ------
    ValueError
        Fewer candidates exist for some scenario than the demo needs (each already-used
        customer is excluded from every later scenario, so the five results are always distinct).
    """
    if rows is None:
        rows = _candidates()
    used: set[str] = set()

    def _pick(rows_: list[CandidateRow], *, expect: Outcome) -> tuple[str, ...]:
        picked = []
        for row in rows_:
            customer_id = row[0]
            if customer_id in used or not _confirms(row, expect=expect):
                continue
            used.add(customer_id)
            picked.append(customer_id)
        return tuple(picked)

    eligible_rows = [row for row in rows if row[5] < Decimal("5000.00") and not row[7]]
    threshold_rows = [row for row in rows if row[5] >= Decimal("5000.00") and not row[7]]
    repeat_complainer_rows = [row for row in rows if row[7]]

    eligible = _pick(eligible_rows, expect=Outcome.ELIGIBLE)
    threshold = _pick(threshold_rows, expect=Outcome.ESCALATE)
    repeat_complainer = _pick(repeat_complainer_rows, expect=Outcome.ESCALATE)

    if len(eligible) < _ELIGIBLE_PERSONAS_NEEDED:
        raise ValueError(
            f"need {_ELIGIBLE_PERSONAS_NEEDED} distinct eligible customers, found {len(eligible)}"
        )
    if not threshold:
        raise ValueError("need 1 amount-above-threshold customer, found none")
    if not repeat_complainer:
        raise ValueError("need 1 repeat-complainer customer, found none")

    # `used` already excludes a customer picked by an earlier scenario from every later one, so
    # the five values below are distinct by construction; no separate uniqueness check is needed.
    return {
        "ana": eligible[0],
        "joao": eligible[1],
        "emma": eligible[2],
        "carlos": threshold[0],
        "mariana": repeat_complainer[0],
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    mapping = select_personas()
    for slug, customer_id in mapping.items():
        logger.info("persona_selected slug=%s customer_id=%s", slug, customer_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
