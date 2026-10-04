"""
Operational Seed Tests
======================

Component: ``pipelines.ops_seed``. Hermetic: a handmade cleaned layer of a small, evenly-spread
customer pool exercises the selection rule end to end; the target and per-stratum minimum are
monkeypatched down so the fixture stays small while still proving the rule prioritises the
customers each stratum needs before it pads with anything else.
"""

from __future__ import annotations

# Standard libraries
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# Third-party libraries
import duckdb
import pytest

# Local modules
from pipelines import ops_seed
from pipelines.ops_seed import (
    COUNTRIES,
    CUSTOMERS_NAME,
    MANIFEST_NAME,
    PRODUCTS_NAME,
    SEGMENTS,
    STRATUM_FLAGS,
    TRANSACTIONS_NAME,
    _mask_email,
    _mask_phone,
    build_seed,
    main,
    render_report,
)

REFERENCE_DATE = date(2026, 6, 18)


def _iso(offset_days: int) -> str:
    """An ISO timestamp ``offset_days`` before or after the reference date."""
    return (REFERENCE_DATE + timedelta(days=offset_days)).isoformat() + " 12:00:00"


# Transaction-level stratum customers: one row of overrides over this plain default, each
# building a single transaction that alone satisfies its own named flag.
_TX_DEFAULTS: dict[str, Any] = {
    "transaction_type": "Purchase",
    "merchant_name": "A Merchant",
    "merchant_category": "Food",
    "amount": 20.0,
    "currency": "USD",
    "amount_usd": 20.0,
    "transaction_status": "Approved",
    "offset": 0,
}
_TX_STRATA: dict[str, dict[str, Any]] = {
    "has_unconvertible_amount": {"amount": 50.0, "currency": "ARS", "amount_usd": None},
    "has_near_5000_transfer": {
        "transaction_type": "Transfer",
        "amount": 5000.0,
        "amount_usd": 5000.0,
    },
    "has_declined": {"transaction_status": "Declined"},
    "has_merchant_null": {"merchant_name": None},
    "has_category_null": {"merchant_category": None},
    "has_tx_60d_before": {"offset": -60},
    "has_tx_90d_before": {"offset": -90},
    "has_tx_120d_before": {"offset": -120},
}


class _Silver:
    """Accumulates the fixture's rows; ``write`` copies them to Parquet under ``root``."""

    def __init__(self) -> None:
        self.customers: list[tuple[Any, ...]] = []
        self.products: list[tuple[Any, ...]] = []
        self.transactions: list[tuple[Any, ...]] = []
        self.complaints: list[tuple[Any, ...]] = []

    def customer(self, customer_id: str, *, country: str, segment: str, status: str) -> None:
        self.customers.append(
            (
                customer_id,
                "First",
                "Last",
                f"{customer_id.lower()}@example.com",
                "+1 555 000 0000",
                None,
                country,
                segment,
                status,
            )
        )

    def product(self, customer_id: str, number: str = "1234567890") -> str:
        product_id = f"PRD-{customer_id}"
        self.products.append((product_id, customer_id, "Cuenta Corriente", number, "Active"))
        return product_id

    def transaction(
        self, customer_id: str, product_id: str, suffix: str = "1", **fields: Any
    ) -> None:
        values = {**_TX_DEFAULTS, **fields}
        self.transactions.append(
            (
                f"TRX-{customer_id}-{suffix}",
                customer_id,
                product_id,
                _iso(values["offset"]),
                values["transaction_type"],
                values["merchant_name"],
                values["merchant_category"],
                values["amount"],
                values["currency"],
                values["amount_usd"],
                values["transaction_status"],
            )
        )

    def complaint(
        self, customer_id: str, suffix: str, *, offset: int, status: str, flag: bool
    ) -> None:
        self.complaints.append(
            (f"CMP-{customer_id}-{suffix}", customer_id, _iso(offset), status, flag)
        )

    def write(self, root: Path) -> Path:
        silver = root / "silver"
        (silver / "silver").mkdir(parents=True)
        con = duckdb.connect()
        con.execute(
            "CREATE TABLE customers (customer_id VARCHAR, first_name VARCHAR, last_name VARCHAR, "
            "email VARCHAR, mobile_phone VARCHAR, landline_phone VARCHAR, country VARCHAR, "
            "segment VARCHAR, customer_status VARCHAR)"
        )
        con.execute(
            "CREATE TABLE products (product_id VARCHAR, customer_id VARCHAR, "
            "product_type VARCHAR, product_number VARCHAR, product_status VARCHAR)"
        )
        con.execute(
            "CREATE TABLE transactions (transaction_id VARCHAR, customer_id VARCHAR, "
            "product_id VARCHAR, transaction_date TIMESTAMP, transaction_type VARCHAR, "
            "merchant_name VARCHAR, merchant_category VARCHAR, amount DECIMAL(15,2), "
            "currency VARCHAR, amount_usd DECIMAL(15,2), transaction_status VARCHAR)"
        )
        con.execute(
            "CREATE TABLE complaints (complaint_id VARCHAR, customer_id VARCHAR, "
            "creation_date TIMESTAMP, status VARCHAR, is_repeat_complainer BOOLEAN)"
        )
        con.execute(
            "CREATE TABLE daily_exchange_rates (date DATE, source_currency VARCHAR, "
            "target_currency VARCHAR, exchange_rate DECIMAL(12,6))"
        )
        con.execute(
            "INSERT INTO daily_exchange_rates VALUES (?, 'COP', 'USD', 0.00025)",
            [REFERENCE_DATE.isoformat()],
        )
        con.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", self.customers)
        con.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?)", self.products)
        con.executemany(
            "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", self.transactions
        )
        con.executemany("INSERT INTO complaints VALUES (?, ?, ?, ?, ?)", self.complaints)
        for name in ("customers", "products", "transactions", "complaints", "daily_exchange_rates"):
            target = silver / "silver" / f"{name}.parquet"
            con.execute(f"COPY {name} TO '{target}' (FORMAT PARQUET)")
        con.close()
        return silver


def _write_silver(root: Path) -> Path:
    """A cleaned layer with a small pool of Active customers, one per segment-country pair,
    plus one customer distinctly tagged per stratum, and three excluded (not Active) customers
    who carry every stratum flag, to prove Active-only holds regardless.

    Segment-country filler customers: ``FILL-<segment>-<country>`` (12 of them).
    Stratum customers: ``STRAT-<flag>`` (one per entry of ``STRATUM_FLAGS``).
    Excluded customers: ``INACTIVE-1`` (Inactive), ``SUSPENDED-1`` (Suspended), ``CLOSED-1``
    (Closed), each carrying every stratum flag a customer can carry.
    """
    silver = _Silver()

    for segment in SEGMENTS:
        for country in COUNTRIES:
            customer_id = f"FILL-{segment}-{country}"
            silver.customer(customer_id, country=country, segment=segment, status="Active")
            silver.transaction(customer_id, silver.product(customer_id))

    for flag, overrides in _TX_STRATA.items():
        customer_id = f"STRAT-{flag}"
        silver.customer(customer_id, country="México", segment="Basic", status="Active")
        silver.transaction(customer_id, silver.product(customer_id), **overrides)

    # is_repeat_complainer: latest complaint on or before the reference date carries the flag.
    cid = "STRAT-is_repeat_complainer"
    silver.customer(cid, country="México", segment="Basic", status="Active")
    silver.product(cid)
    silver.complaint(cid, "old", offset=-100, status="Closed", flag=False)
    silver.complaint(cid, "new", offset=-1, status="Closed", flag=True)

    # has_open_case
    cid = "STRAT-has_open_case"
    silver.customer(cid, country="México", segment="Basic", status="Active")
    silver.product(cid)
    silver.complaint(cid, "1", offset=-10, status="Open", flag=False)

    # A customer whose only repeat-complainer-flagged complaint is AFTER the reference date must
    # NOT count: the latest complaint on or before the reference date is the unflagged one.
    silver.customer("LATE-COMPLAINT", country="México", segment="Basic", status="Active")
    silver.product("LATE-COMPLAINT")
    silver.complaint("LATE-COMPLAINT", "before", offset=-5, status="Closed", flag=False)
    silver.complaint("LATE-COMPLAINT", "after", offset=5, status="Closed", flag=True)

    # Excluded: every stratum flag, but not Active. Proves Active-only holds regardless of need.
    for customer_id, status in (
        ("INACTIVE-1", "Inactive"),
        ("SUSPENDED-1", "Suspended"),
        ("CLOSED-1", "Closed"),
    ):
        silver.customer(customer_id, country="México", segment="Basic", status=status)
        product_id = silver.product(customer_id)
        silver.transaction(
            customer_id,
            product_id,
            transaction_type="Transfer",
            merchant_name=None,
            merchant_category=None,
            amount=5000.0,
            amount_usd=5000.0,
            transaction_status="Declined",
        )
        silver.complaint(customer_id, "1", offset=-1, status="Open", flag=True)

    return silver.write(root)


@pytest.fixture(autouse=True)
def _small_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture has ~28 Active customers; a small target and minimum keep the rule testable
    without requiring hundreds of synthetic rows, while still forcing prioritisation over padding
    (the target is below the pool size)."""
    monkeypatch.setattr(ops_seed, "TARGET_CUSTOMERS", 20)
    monkeypatch.setattr(ops_seed, "MIN_PER_STRATUM", 1)


def _read_parquet(path: Path) -> list[dict[str, Any]]:
    con = duckdb.connect()
    try:
        cursor = con.execute(f"SELECT * FROM read_parquet('{path}')")  # noqa: S608 - a temp path
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        con.close()


def test_every_stratum_customer_is_selected_and_counted_in_coverage(tmp_path: Path) -> None:
    silver = _write_silver(tmp_path / "base")
    manifest = build_seed(silver, tmp_path / "gold", code_version="test")
    seed_customers = {
        row["customer_id"] for row in _read_parquet(tmp_path / "gold" / CUSTOMERS_NAME)
    }

    for flag in STRATUM_FLAGS:
        assert f"STRAT-{flag}" in seed_customers, f"{flag}'s stratum customer was not selected"
        assert manifest.coverage[flag] >= 1


def test_repeat_complainer_uses_the_latest_complaint_on_or_before_the_reference_date(
    tmp_path: Path,
) -> None:
    """The point-in-time rule: a later complaint's flag is never consulted."""
    silver = _write_silver(tmp_path / "base")
    con = duckdb.connect()
    try:
        for name in ("customers", "products", "transactions", "complaints", "daily_exchange_rates"):
            source = f"'{silver / 'silver' / (name + '.parquet')}'"
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet({source})")  # noqa: S608
        cursor = con.execute(ops_seed._flags_query(REFERENCE_DATE.isoformat()))
        columns = [column[0] for column in cursor.description]
        flags = {row[0]: dict(zip(columns, row, strict=True)) for row in cursor.fetchall()}
    finally:
        con.close()

    # Its latest complaint on or before the reference date carries the flag.
    assert flags["STRAT-is_repeat_complainer"]["is_repeat_complainer"] is True
    # Its only flagged complaint is after the reference date; the earlier one is unflagged, so
    # the customer does not carry the flag, even though a later read of the source would show one.
    assert flags["LATE-COMPLAINT"]["is_repeat_complainer"] is False


def _customers_output(tmp_path: Path) -> dict[str, bool]:
    silver = _write_silver(tmp_path / "base")
    build_seed(silver, tmp_path / "gold", code_version="test")
    return {
        row["customer_id"]: row["is_repeat_complainer"]
        for row in _read_parquet(tmp_path / "gold" / CUSTOMERS_NAME)
    }


def test_a_seeded_repeat_complainer_carries_the_flag_in_the_written_customers_output(
    tmp_path: Path,
) -> None:
    """The flag used to choose which customers the seed carries must also reach the seed's own
    ``customers.parquet`` output, not be discarded after selection: a policy decision computed
    from that output is otherwise structurally unable to ever see a real repeat complainer."""
    output = _customers_output(tmp_path)

    assert output["STRAT-is_repeat_complainer"] is True


def test_a_customer_whose_only_flagged_complaint_is_too_late_carries_false(
    tmp_path: Path,
) -> None:
    output = _customers_output(tmp_path)

    assert output["LATE-COMPLAINT"] is False


def test_active_only_holds_even_for_a_customer_carrying_several_stratum_flags(
    tmp_path: Path,
) -> None:
    """Each excluded customer carries several strata at once (open case, repeat complainer,
    a near-5,000 transfer, declined, merchant- and category-null) — enough that a rule filtering
    Active status only after selection would very likely still pick one of them."""
    silver = _write_silver(tmp_path / "base")
    build_seed(silver, tmp_path / "gold", code_version="test")
    seed_customers = {
        row["customer_id"] for row in _read_parquet(tmp_path / "gold" / CUSTOMERS_NAME)
    }
    assert seed_customers.isdisjoint({"INACTIVE-1", "SUSPENDED-1", "CLOSED-1"})

    statuses = {row["customer_status"] for row in _read_parquet(tmp_path / "gold" / CUSTOMERS_NAME)}
    assert statuses == {"Active"}


def test_every_segment_and_country_is_represented(tmp_path: Path) -> None:
    silver = _write_silver(tmp_path / "base")
    manifest = build_seed(silver, tmp_path / "gold", code_version="test")
    for segment in SEGMENTS:
        for country in COUNTRIES:
            assert manifest.coverage[f"segment={segment},country={country}"] >= 1


def test_coverage_requirements_can_exceed_the_target_customer_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TARGET_CUSTOMERS is a floor the padding step reaches, never a ceiling the stratum and
    segment-country guarantees are trimmed back to fit; the fixture alone needs more than one
    customer to cover every stratum and every segment-country pair."""
    monkeypatch.setattr(ops_seed, "TARGET_CUSTOMERS", 1)
    silver = _write_silver(tmp_path / "base")
    manifest = build_seed(silver, tmp_path / "gold", code_version="test")
    assert manifest.selected_customers > 1
    for flag in STRATUM_FLAGS:
        assert manifest.coverage[flag] >= 1


def test_no_full_email_or_phone_appears_in_the_seed(tmp_path: Path) -> None:
    """The written output is scanned for every full contact value the source held; none appears."""
    silver = _write_silver(tmp_path / "base")
    build_seed(silver, tmp_path / "gold", code_version="test")
    content = (tmp_path / "gold" / CUSTOMERS_NAME).read_bytes()

    for row in _read_parquet(silver / "silver" / "customers.parquet"):
        if row["email"]:
            assert row["email"].encode("utf-8") not in content
        for phone in (row["mobile_phone"], row["landline_phone"]):
            if phone:
                assert phone.encode("utf-8") not in content


def test_mask_email_and_mask_phone_never_return_the_input_unchanged() -> None:
    assert _mask_email("someone@example.com") != "someone@example.com"
    assert _mask_email("someone@example.com") == "s****@example.com"
    assert _mask_email(None) is None
    assert _mask_email("noatsign") == "****"
    assert _mask_phone("+57 312 876 1601") == "****01"
    assert _mask_phone(None) is None
    assert _mask_phone("12") == "****12"
    assert _mask_phone("1") == "****1"


def test_last4_is_always_exactly_four_digits(tmp_path: Path) -> None:
    silver = _write_silver(tmp_path / "base")
    build_seed(silver, tmp_path / "gold", code_version="test")
    for row in _read_parquet(tmp_path / "gold" / PRODUCTS_NAME):
        assert len(row["last4"]) == 4
        assert row["last4"].isdigit()


def test_amount_usd_and_provenance_match_pipelines_amounts(tmp_path: Path) -> None:
    """The seed's transactions use the same rule as risk_features, imported not re-derived."""
    silver = _write_silver(tmp_path / "base")
    build_seed(silver, tmp_path / "gold", code_version="test")
    rows = {
        row["transaction_id"]: row for row in _read_parquet(tmp_path / "gold" / TRANSACTIONS_NAME)
    }
    unconvertible = rows["TRX-STRAT-has_unconvertible_amount-1"]
    assert unconvertible["amount_usd"] is None
    assert unconvertible["amount_usd_provenance"] == "unknown"

    transfer = rows["TRX-STRAT-has_near_5000_transfer-1"]
    assert float(transfer["amount_usd"]) == 5000.0
    assert transfer["amount_usd_provenance"] == "reported"


def test_cases_are_never_produced_by_the_seed(tmp_path: Path) -> None:
    """See the module's Limitations: cases starts empty, seeded live only."""
    silver = _write_silver(tmp_path / "base")
    build_seed(silver, tmp_path / "gold", code_version="test")
    assert not (tmp_path / "gold" / "cases.parquet").exists()


def test_the_manifest_records_the_reference_date_from_the_data(tmp_path: Path) -> None:
    silver = _write_silver(tmp_path / "base")
    manifest = build_seed(silver, tmp_path / "gold", code_version="test")
    assert manifest.reference_date == REFERENCE_DATE.isoformat()
    written = json.loads((tmp_path / "gold" / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert written["reference_date"] == REFERENCE_DATE.isoformat()


def test_the_same_inputs_produce_byte_identical_outputs(tmp_path: Path) -> None:
    silver = _write_silver(tmp_path / "base")
    build_seed(silver, tmp_path / "g1", code_version="test")
    build_seed(silver, tmp_path / "g2", code_version="test")
    for name in (CUSTOMERS_NAME, PRODUCTS_NAME, TRANSACTIONS_NAME, MANIFEST_NAME):
        assert (tmp_path / "g1" / name).read_bytes() == (tmp_path / "g2" / name).read_bytes()


def test_a_missing_cleaned_table_stops_the_build(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_seed(tmp_path / "none", tmp_path / "gold", code_version="test")
    assert not (tmp_path / "gold").exists()


def test_an_empty_transactions_table_refuses_for_lack_of_a_reference_date(tmp_path: Path) -> None:
    """Every cleaned table is present (so the file-existence check passes) but ``transactions``
    holds no rows, so there is no newest transaction instant to read a reference date from."""
    silver = _write_silver(tmp_path / "base")
    target = silver / "silver" / "transactions.parquet"
    temporary = target.with_suffix(".parquet.tmp")
    con = duckdb.connect()
    try:
        con.execute(
            f"COPY (SELECT * FROM read_parquet('{target}') WHERE false) "  # noqa: S608
            f"TO '{temporary}' (FORMAT PARQUET)"
        )
    finally:
        con.close()
    temporary.replace(target)

    with pytest.raises(ValueError, match="no reference date"):
        build_seed(silver, tmp_path / "gold", code_version="test")
    assert not (tmp_path / "gold" / CUSTOMERS_NAME).exists()


def test_the_report_states_the_seed_is_curated_and_shows_seed_and_source_rates(
    tmp_path: Path,
) -> None:
    silver = _write_silver(tmp_path / "base")
    manifest = build_seed(silver, tmp_path / "gold", code_version="test")
    report = render_report(manifest)
    assert "curated" in report
    assert "Seed" in report and "Source" in report
    for flag in STRATUM_FLAGS:
        assert f"`{flag}`" in report


def test_the_command_writes_the_report_and_is_idempotent(tmp_path: Path) -> None:
    silver = _write_silver(tmp_path / "base")
    exit_code = main(
        [
            "--silver",
            str(silver),
            "--gold",
            str(tmp_path / "gold"),
            "--report",
            str(tmp_path / "report.md"),
            "--code-version",
            "test",
        ]
    )
    assert exit_code == 0
    first = (tmp_path / "report.md").read_bytes()
    exit_code = main(
        [
            "--silver",
            str(silver),
            "--gold",
            str(tmp_path / "gold"),
            "--report",
            str(tmp_path / "report.md"),
            "--code-version",
            "test",
        ]
    )
    assert exit_code == 0
    assert (tmp_path / "report.md").read_bytes() == first


def test_the_command_fails_with_one_on_a_missing_layer(tmp_path: Path) -> None:
    exit_code = main(
        [
            "--silver",
            str(tmp_path / "none"),
            "--gold",
            str(tmp_path / "gold"),
            "--report",
            str(tmp_path / "report.md"),
        ]
    )
    assert exit_code == 1
    assert not (tmp_path / "report.md").exists()
