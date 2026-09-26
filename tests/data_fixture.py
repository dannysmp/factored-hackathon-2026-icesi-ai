"""
Raw Data Fixture Builder
========================

Writes small CSV datasets in the raw layout (dimension files, and fact files partitioned as
``<table>/year=YYYY/month=MM/day=DD/<table>_YYYYMMDD.csv``) so the profiler and the pipeline
stages can be tested against data whose every defect is known.

Every value here is invented for the tests; nothing is copied from real data.
"""

from __future__ import annotations

# Standard libraries
import csv  # Write quoted CSV rows
from collections.abc import Mapping, Sequence  # Row and column types
from datetime import date  # Partition day
from pathlib import Path  # Output location

# Local modules
from pipelines.sources import TableSpec, table  # Declared columns of each table

# Byte-order mark written at the start of every file, like the source exports do.
BOM = "﻿"


def write_csv(path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> Path:
    """Write ``rows`` under ``columns``; columns a row does not mention are left empty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(BOM)
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(column, "") for column in columns])
    return path


def write_dimension(
    data_dir: Path, spec: TableSpec | str, rows: Sequence[Mapping[str, str]]
) -> Path:
    """Write a single-file table such as ``customers.csv``."""
    resolved = table(spec) if isinstance(spec, str) else spec
    return write_csv(data_dir / f"{resolved.name}.csv", resolved.column_names, rows)


def write_partition(
    data_dir: Path,
    spec: TableSpec | str,
    day: date,
    rows: Sequence[Mapping[str, str]],
    extra_columns: Sequence[str] = (),
) -> Path:
    """Write one daily partition file; ``extra_columns`` simulates an evolved schema."""
    resolved = table(spec) if isinstance(spec, str) else spec
    path = (
        data_dir
        / resolved.name
        / f"year={day:%Y}"
        / f"month={day:%m}"
        / f"day={day:%d}"
        / f"{resolved.name}_{day:%Y%m%d}.csv"
    )
    return write_csv(path, [*resolved.column_names, *extra_columns], rows)


def build_dispute_dataset(data_dir: Path) -> None:
    """Build the reference dataset used by the profiler tests.

    Known properties (asserted by the tests):

    * ``transactions``: six rows over two partitions with one missing day between them; four
      distinct keys, one identical re-delivery, one conflicting re-delivery, one orphan product,
      one orphan customer, one amount whose USD value disagrees with the daily rate, and a
      second-day file that carries an extra column.
    * ``customers``: one missing NOT NULL value, one integer written with a decimal point and one
      value with damaged text encoding.
    """
    write_dimension(data_dir, "branches", [{"branch_id": "B1", "branch_code": "S1"}])
    write_dimension(
        data_dir,
        "customers",
        [
            {
                "customer_id": "C1",
                "document_number": "D1",
                "first_name": "Ana",
                "credit_score": "700",
                "registration_branch_id": "B1",
                "address": "Calle 5 número 3, barrio centro",
            },
            {
                "customer_id": "C2",
                "document_number": "",
                "first_name": "José",
                "credit_score": "701.0",
                "registration_branch_id": "B-MISSING",
                "address": "Calle 9 número 4, barrio bajo Ã© alto",
            },
        ],
    )
    write_dimension(
        data_dir,
        "products",
        [
            {"product_id": "P1", "customer_id": "C1", "product_type": "Credit Card"},
            {"product_id": "P2", "customer_id": "C2", "product_type": "Debit Card"},
        ],
    )
    write_dimension(
        data_dir,
        "daily_exchange_rates",
        [
            {
                "date": "2025-01-10",
                "source_currency": "MXN",
                "target_currency": "USD",
                "exchange_rate": "0.05",
            },
            {
                "date": "2025-01-12",
                "source_currency": "MXN",
                "target_currency": "USD",
                "exchange_rate": "0.05",
            },
        ],
    )

    first_day, second_day = date(2025, 1, 10), date(2025, 1, 12)

    def transaction(key: str, **values: str) -> dict[str, str]:
        base = {
            "transaction_id": key,
            "transaction_date": "2025-01-10 08:00:00",
            "process_date": "2025-01-10",
            "product_id": "P1",
            "customer_id": "C1",
            "transaction_type": "Purchase",
            "amount": "100.00",
            "currency": "MXN",
            "amount_usd": "5.00",
            "channel": "App",
            "transaction_country": "Mexico",
            "transaction_status": "Approved",
            "is_fraud": "False",
        }
        return {**base, **values}

    write_partition(
        data_dir,
        "transactions",
        first_day,
        [
            transaction("T1"),
            transaction(
                "T2",
                product_id="P2",
                customer_id="C2",
                amount="200.00",
                currency="USD",
                amount_usd="200.00",
                is_fraud="True",
            ),
            transaction("T3", product_id="P-MISSING", amount="10.00", amount_usd=""),
        ],
    )
    write_partition(
        data_dir,
        "transactions",
        second_day,
        [
            transaction("T1", process_date="2025-01-12"),
            transaction(
                "T2",
                process_date="2025-01-12",
                product_id="P2",
                customer_id="C2",
                amount="250.00",
                currency="USD",
                amount_usd="250.00",
                is_fraud="True",
            ),
            transaction(
                "T4",
                transaction_date="2025-01-12 09:00:00",
                process_date="2025-01-12",
                customer_id="C-GHOST",
                amount="50.00",
                amount_usd="9.99",
            ),
        ],
        extra_columns=("new_field",),
    )

    write_partition(
        data_dir,
        "complaints",
        first_day,
        [
            {
                "complaint_id": "K1",
                "creation_date": "2025-01-10 10:00:00",
                "process_date": "2025-01-10",
                "customer_id": "C1",
                "category": "Fees",
                "sla_breached": "True",
                "is_repeat_complainer": "False",
            },
            {
                "complaint_id": "K2",
                "creation_date": "2025-01-11 10:00:00",
                "process_date": "2025-01-10",
                "customer_id": "C2",
                "category": "Fraud",
                "sla_breached": "False",
                "is_repeat_complainer": "True",
            },
        ],
    )
    write_partition(
        data_dir,
        "call_center_interactions",
        first_day,
        [
            {
                "interaction_id": "I1",
                "interaction_date": "2025-01-10 09:00:00",
                "process_date": "2025-01-10",
                "customer_id": "C1",
                "contact_reason": "Card dispute",
                "reason_category": "Complaint",
                "has_transcript": "True",
            },
            {
                "interaction_id": "I2",
                "interaction_date": "2025-01-10 09:30:00",
                "process_date": "2025-01-10",
                "customer_id": "C2",
                "contact_reason": "Balance",
                "reason_category": "Transactional",
                "has_transcript": "False",
            },
        ],
    )
    write_partition(
        data_dir,
        "call_transcripts",
        first_day,
        [
            {
                "transcript_id": "X1",
                "interaction_id": "I1",
                "process_date": "2025-01-10",
                "customer_id": "C1",
            }
        ],
    )


# -----------------------------------------------------------------------------
# Contract-satisfying rows
# -----------------------------------------------------------------------------


_DEFAULTS = {
    "DATE": "2025-01-10",
    "TIMESTAMP": "2025-01-10 08:00:00",
    "TIME": "09:00:00",
    "INTEGER": "500",
    "BOOLEAN": "False",
}


def _default_value(name: str, dtype: str, allowed: frozenset[str] | None) -> str:
    """A value of ``dtype`` that satisfies the contract (first allowed value when coded)."""
    if allowed:
        return sorted(allowed)[0]
    kind = dtype.upper()
    if kind.startswith("DECIMAL"):
        return "10.00"
    return _DEFAULTS.get(kind, f"{name}-value")


def valid_row(table_name: str, **overrides: str) -> dict[str, str]:
    """A complete row of ``table_name`` that satisfies structure and contract.

    Every declared column is filled (including the optional ones), so a test names only the
    values that matter to it; ``overrides`` replace defaults, and an override of ``""`` writes a
    missing value.
    """
    from contracts.v1 import contract_for  # noqa: PLC0415 - only the cleaning tests need it

    spec = table(table_name)
    allowed = {rule.column: rule.values for rule in contract_for(table_name).allowed}
    row = {
        column.name: _default_value(column.name, column.dtype, allowed.get(column.name))
        for column in spec.columns
    }
    return {**row, **overrides}


def build_clean_dataset(data_dir: Path) -> None:
    """Build a small dataset in which every row satisfies the contract.

    Two branches, two customers, two products and one exchange rate, plus transactions in two
    daily partitions. Tests add defects or later deliveries on top of it.
    """
    write_dimension(
        data_dir,
        "branches",
        [valid_row("branches", branch_id="B1"), valid_row("branches", branch_id="B2")],
    )
    write_dimension(
        data_dir,
        "customers",
        [
            valid_row(
                "customers", customer_id="C1", document_number="D1", registration_branch_id="B1"
            ),
            valid_row(
                "customers", customer_id="C2", document_number="D2", registration_branch_id="B2"
            ),
        ],
    )
    write_dimension(
        data_dir,
        "products",
        [
            valid_row(
                "products",
                product_id="P1",
                customer_id="C1",
                product_number="N1",
                opening_branch_id="B1",
            ),
            valid_row(
                "products",
                product_id="P2",
                customer_id="C2",
                product_number="N2",
                opening_branch_id="B2",
            ),
        ],
    )
    write_dimension(
        data_dir,
        "daily_exchange_rates",
        [valid_row("daily_exchange_rates", source_currency="MXN", target_currency="USD")],
    )
    write_partition(
        data_dir,
        "transactions",
        date(2025, 1, 10),
        [
            valid_row("transactions", transaction_id="T1", product_id="P1", customer_id="C1"),
            valid_row("transactions", transaction_id="T2", product_id="P2", customer_id="C2"),
        ],
    )
    write_partition(
        data_dir,
        "transactions",
        date(2025, 1, 12),
        [
            valid_row(
                "transactions",
                transaction_id="T3",
                product_id="P1",
                customer_id="C1",
                process_date="2025-01-12",
            )
        ],
    )


def build_service_dataset(data_dir: Path) -> None:
    """Build the clean dataset plus service contacts, complaints and surveys of known values.

    Five contacts (transactional of 300 and 600 seconds and one without duration, wait or score;
    a complaint of 120 seconds; one without a reason category), eleven complaints of which six are
    disputes (resolved in 10 and 20 days, one of them with the SLA
    breached and a repeat complainer; closed in 30 days; open; rejected; escalated with days
    recorded that the overall figure must ignore), one complaint that only looks like a dispute
    (another subcategory), one of the neighbouring undue-charge subcategory, one of the Transactions
    category without a subcategory, one of another category without a subcategory, one in another
    category, and a survey per contact plus one with no contact and one with a dangling reference.
    """
    build_clean_dataset(data_dir)
    write_dimension(data_dir, "service_agents", [valid_row("service_agents", agent_id="A1")])
    january, february = date(2025, 1, 10), date(2025, 2, 10)

    def contact(name: str, when: date, **values: str) -> dict[str, str]:
        return valid_row(
            "call_center_interactions",
            interaction_id=name,
            interaction_date=f"{when:%Y-%m-%d} 09:00:00",
            process_date=f"{when:%Y-%m-%d}",
            customer_id="C1",
            agent_id="A1",
            **values,
        )

    write_partition(
        data_dir,
        "call_center_interactions",
        january,
        [
            contact(
                "I1",
                january,
                reason_category="Transaccional",
                duration_seconds="300",
                wait_time_seconds="60",
                was_resolved="True",
                was_escalated="False",
                requires_followup="False",
                detected_sentiment="Neutral",
                sentiment_score="0.00",
            ),
            contact(
                "I4",
                january,
                reason_category="Transaccional",
                duration_seconds="",
                wait_time_seconds="",
                detected_sentiment="Positivo",
                sentiment_score="",
            ),
            contact(
                "I5",
                january,
                reason_category="",
                duration_seconds="60",
                wait_time_seconds="10",
                detected_sentiment="Neutral",
                sentiment_score="0.00",
            ),
            contact(
                "I3",
                january,
                reason_category="Queja",
                duration_seconds="120",
                wait_time_seconds="30",
                was_resolved="False",
                was_escalated="True",
                requires_followup="True",
                detected_sentiment="Muy Negativo",
                sentiment_score="-0.85",
            ),
        ],
    )
    write_partition(
        data_dir,
        "call_center_interactions",
        february,
        [
            contact(
                "I2",
                february,
                reason_category="Transaccional",
                duration_seconds="600",
                wait_time_seconds="120",
                was_resolved="False",
                was_escalated="False",
                requires_followup="True",
                detected_sentiment="Negativo",
                sentiment_score="-0.50",
            )
        ],
    )

    def complaint(name: str, when: date, **values: str) -> dict[str, str]:
        return valid_row(
            "complaints",
            complaint_id=name,
            creation_date=f"{when:%Y-%m-%d} 09:00:00",
            process_date=f"{when:%Y-%m-%d}",
            customer_id="C1",
            assigned_agent_id="A1",
            affected_product_id="P1",
            related_branch_id="B1",
            origin_interaction_id="",
            **values,
        )

    dispute = {"category": "Transactions", "subcategory": "Cargo no reconocido"}
    write_partition(
        data_dir,
        "complaints",
        january,
        [
            complaint(
                "K1",
                january,
                **dispute,
                status="Resolved",
                resolution_days="10",
                sla_breached="False",
                is_repeat_complainer="False",
                currency="USD",
                claimed_amount="100.00",
            ),
            complaint(
                "K2",
                january,
                **dispute,
                status="Resolved",
                resolution_days="20",
                sla_breached="True",
                is_repeat_complainer="True",
                currency="MXN",
                claimed_amount="200.00",
            ),
            complaint(
                "K7",
                january,
                **dispute,
                status="Rejected",
                resolution_days="",
                sla_breached="False",
                is_repeat_complainer="False",
                currency="",
                claimed_amount="",
            ),
        ],
    )
    write_partition(
        data_dir,
        "complaints",
        february,
        [
            complaint(
                "K3",
                february,
                **dispute,
                status="In Process",
                resolution_days="",
                sla_breached="False",
                is_repeat_complainer="False",
                currency="",
                claimed_amount="",
                first_response_date="",
            ),
            complaint(
                "K6",
                february,
                **dispute,
                status="Closed",
                resolution_days="30",
                sla_breached="False",
                is_repeat_complainer="False",
                currency="ARS",
                claimed_amount="300.00",
            ),
            complaint(
                "K8",
                february,
                **dispute,
                status="Escalated",
                resolution_days="40",
                sla_breached="False",
                is_repeat_complainer="False",
                currency="",
                claimed_amount="",
            ),
            complaint(
                "K4",
                february,
                category="Transactions",
                subcategory="Otro",
                status="Open",
                resolution_days="",
            ),
            complaint(
                "K9",
                february,
                category="Fees",
                subcategory="Cobro indebido",
                status="Open",
                resolution_days="",
            ),
            complaint(
                "K11",
                february,
                category="Fees",
                subcategory="",
                status="Open",
                resolution_days="",
            ),
            complaint(
                "K10",
                february,
                category="Transactions",
                subcategory="",
                status="Open",
                resolution_days="",
            ),
            complaint(
                "K5",
                february,
                category="Fees",
                subcategory="Cargo no reconocido",
                status="Open",
                resolution_days="",
            ),
        ],
    )

    def survey(name: str, contact_id: str, score: str, when: date) -> dict[str, str]:
        return valid_row(
            "satisfaction_surveys",
            survey_id=name,
            survey_date=f"{when:%Y-%m-%d} 10:00:00",
            process_date=f"{when:%Y-%m-%d}",
            interaction_id=contact_id,
            customer_id="C1",
            agent_id="A1",
            main_score=score,
        )

    write_partition(
        data_dir,
        "satisfaction_surveys",
        january,
        [
            survey("S1", "I1", "5", january),
            survey("S3", "I3", "1", january),
            survey("S4", "I4", "4", january),
            survey("S5", "", "2", january),
            survey("S6", "I99", "3", january),
            survey("S7", "I5", "5", january),
        ],
    )
    write_partition(data_dir, "satisfaction_surveys", february, [survey("S2", "I2", "3", february)])
