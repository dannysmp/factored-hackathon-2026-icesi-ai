"""
Risk Feature Mart Tests
=======================

Component: ``pipelines.risk_features``. Hermetic: a handmade cleaned layer of a few transactions
whose features are derived by hand, so every window, conversion and period boundary is checked
against a number worked out on paper; the leakage tests append later transactions and change later
labels to prove earlier rows do not move.
"""

from __future__ import annotations

# Standard libraries
import hashlib  # Byte-identity of the outputs
import json  # Manifest contents
import math  # Independent haversine for the distance check
from datetime import date  # Boundaries in the split
from pathlib import Path  # Temporary locations
from typing import Any  # Rows as dictionaries

# Third-party libraries
import duckdb  # Handmade cleaned layer and reading the mart back
import pytest  # Test runner and fixtures

# Local modules
from pipelines.risk_features import (
    DEFAULT_SPLIT,
    EXCLUDED,
    FEATURES,
    PROXIES,
    SplitConfig,
    build_risk_features,
    load_split,
    main,
    render_report,
)

SPLIT = SplitConfig(date(2025, 3, 31), date(2025, 9, 30))
MEXICO_CITY = (19.4326, -99.1332)
GUADALAJARA = (20.6597, -103.3496)

# customer, id, timestamp, amount, currency, reported usd, country, category, place, fraud, score
Row = tuple[
    str,
    str,
    str,
    float,
    str,
    float | None,
    str,
    str | None,
    tuple[float, float] | None,
    bool,
    float,
]

BASE: list[Row] = [
    ("C1", "T1", "2025-01-10 09:00:00", 1000, "MXN", None, "México", "Food", MEXICO_CITY, False, 5),
    ("C1", "T2", "2025-01-10 15:00:00", 200, "USD", 200, "USA", None, None, False, 12),
    ("C1", "T3", "2025-01-13 09:00:00", 2000, "MXN", None, "México", "Food", GUADALAJARA, True, 88),
    ("C1", "T4", "2025-01-10 09:00:00", 10, "USD", 10, "México", "Food", None, False, 30),
    ("C2", "T6", "2025-03-31 23:59:59", 50, "COP", 25, "Colombia", "Services", None, False, 7),
    ("C2", "T5", "2025-04-01 10:00:00", 60, "COP", 30, "Colombia", "Services", None, False, 7),
    ("C2", "T7", "2025-09-30 23:59:59", 70, "COP", 35, "Colombia", "Services", None, True, 60),
    ("C2", "T8", "2025-10-01 00:00:00", 80, "COP", 40, "Colombia", "Services", None, False, 9),
    # a customer that is missing from the customers table
    ("C3", "T12", "2025-01-20 12:00:00", 1, "USD", 1, "USA", "Food", None, False, 3),
]


def _write_silver(root: Path, rows: list[Row]) -> Path:
    """A cleaned layer holding exactly ``rows``, one customer per country."""
    silver = root / "silver"
    (silver / "silver").mkdir(parents=True)
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE transactions (transaction_id VARCHAR, customer_id VARCHAR, "
        "transaction_date TIMESTAMP, amount DECIMAL(15,2), currency VARCHAR, "
        "amount_usd DECIMAL(15,2), channel VARCHAR, transaction_type VARCHAR, "
        "merchant_category VARCHAR, transaction_country VARCHAR, latitude DECIMAL(10,7), "
        "longitude DECIMAL(10,7), is_fraud BOOLEAN, fraud_score DECIMAL(5,2), "
        "response_code VARCHAR, transaction_status VARCHAR)"
    )
    for (
        customer,
        ident,
        stamp,
        amount,
        currency,
        usd,
        country,
        category,
        place,
        fraud,
        score,
    ) in rows:
        con.execute(
            "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, 'POS', 'Purchase', "
            "?, ?, ?, ?, ?, ?, '00', 'Approved')",
            [
                ident,
                customer,
                stamp,
                amount,
                currency,
                usd,
                category,
                country,
                None if place is None else place[0],
                None if place is None else place[1],
                fraud,
                score,
            ],
        )
    con.execute(
        "CREATE TABLE customers (customer_id VARCHAR, country VARCHAR, last_updated TIMESTAMP)"
    )
    # C2 is updated the day after the training period; C4 (no transactions) exactly on its last day
    con.execute(
        "INSERT INTO customers VALUES ('C1', 'México', '2025-06-01 00:00:00'), "
        "('C2', 'Colombia', '2025-04-01 00:00:00'), ('C4', 'Colombia', '2025-03-31 12:00:00')"
    )
    con.execute(
        "CREATE TABLE daily_exchange_rates (date DATE, source_currency VARCHAR, "
        "target_currency VARCHAR, exchange_rate DECIMAL(12,6))"
    )
    # MXN is known only for the day of the first transaction
    con.execute(
        "INSERT INTO daily_exchange_rates VALUES ('2025-01-10', 'MXN', 'USD', 0.05), "
        "('2025-01-10', 'MXN', 'COP', 200.0), ('2025-01-10', 'USD', 'MXN', 20.0)"
    )
    for name in ("transactions", "customers", "daily_exchange_rates"):
        con.execute(f"COPY {name} TO '{silver / 'silver' / (name + '.parquet')}' (FORMAT PARQUET)")
    con.close()
    return silver


def _mart(gold: Path) -> dict[str, dict[str, Any]]:
    con = duckdb.connect()
    cursor = con.execute(
        f"SELECT * FROM read_parquet('{gold / 'risk_features.parquet'}')"  # noqa: S608 - a temp path
    )
    names = [column[0] for column in cursor.description]
    result = {row[0]: dict(zip(names, row, strict=True)) for row in cursor.fetchall()}
    con.close()
    return result


@pytest.fixture
def built(tmp_path: Path) -> tuple[Path, dict[str, dict[str, Any]]]:
    """The gold directory and the mart, built from the base rows."""
    silver = _write_silver(tmp_path / "base", BASE)
    gold = tmp_path / "gold"
    build_risk_features(silver, gold, SPLIT, code_version="test")
    return gold, _mart(gold)


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


# -----------------------------------------------------------------------------
# Features derived by hand
# -----------------------------------------------------------------------------


def test_the_amount_in_dollars_is_reported_converted_or_unavailable(built: Any) -> None:
    """A stated amount is kept; otherwise the day's rate applies; without a rate it is empty."""
    _, mart = built

    assert (mart["T2"]["amount_usd"], mart["T2"]["amount_usd_source"]) == (200.0, "reported")
    assert (mart["T1"]["amount_usd"], mart["T1"]["amount_usd_source"]) == (50.0, "converted")
    assert (mart["T3"]["amount_usd"], mart["T3"]["amount_usd_source"]) == (None, "unavailable")


def test_calendar_features_follow_the_transaction_time(built: Any) -> None:
    """2025-01-10 is a Friday; 2025-01-11 would be the weekend."""
    _, mart = built

    assert (mart["T1"]["hour"], mart["T1"]["day_of_week"], mart["T1"]["is_weekend"]) == (
        9,
        5,
        False,
    )
    assert (mart["T2"]["hour"], mart["T2"]["day_of_week"]) == (15, 5)
    assert mart["T3"]["day_of_week"] == 1  # Monday 2025-01-13


def test_a_weekend_is_saturday_or_sunday(tmp_path: Path) -> None:
    """Days 6 and 7 are the weekend; Friday and Monday are not."""
    rows: list[Row] = [
        ("C1", f"D{n}", f"2025-01-{10 + n} 12:00:00", 1, "USD", 1, "México", "Food", None, False, 1)
        for n in range(0, 5)
    ]
    silver = _write_silver(tmp_path / "s", rows)
    build_risk_features(silver, tmp_path / "g", SPLIT, code_version="test")

    mart = _mart(tmp_path / "g")

    assert [mart[f"D{n}"]["is_weekend"] for n in range(5)] == [False, True, True, False, False]


def test_categories_country_mismatch_and_unknowns(built: Any) -> None:
    """A missing merchant category becomes `unknown`; the countries are compared."""
    _, mart = built

    assert mart["T2"]["merchant_category"] == "unknown"
    assert (mart["T2"]["transaction_country"], mart["T2"]["customer_country"]) == ("USA", "México")
    assert mart["T2"]["country_mismatch"] is True
    assert mart["T1"]["country_mismatch"] is False and mart["T5"]["country_mismatch"] is False


def test_a_transaction_of_a_customer_missing_from_the_customer_table_is_kept(built: Any) -> None:
    """The join never drops a transaction: its customer country and mismatch are empty."""
    _, mart = built

    assert mart["T12"]["customer_country"] is None
    assert mart["T12"]["country_mismatch"] is None
    assert mart["T12"]["transaction_country"] == "USA"


def test_velocity_windows_count_only_earlier_transactions_and_exclude_ties(built: Any) -> None:
    """T1 and T4 share a timestamp: neither sees the other; T2 sees both; T3 sees them in 7 days."""
    _, mart = built

    assert (mart["T1"]["tx_count_24h"], mart["T4"]["tx_count_24h"]) == (0, 0)
    assert (mart["T2"]["tx_count_24h"], mart["T2"]["tx_sum_usd_24h"]) == (2, 60.0)
    assert (mart["T2"]["tx_count_7d"], mart["T2"]["tx_sum_usd_7d"]) == (2, 60.0)
    assert (mart["T3"]["tx_count_24h"], mart["T3"]["tx_sum_usd_24h"]) == (0, 0.0)
    assert (mart["T3"]["tx_count_7d"], mart["T3"]["tx_sum_usd_7d"]) == (3, 260.0)


def test_the_windows_include_the_last_second_of_their_span_and_exclude_the_next(
    tmp_path: Path,
) -> None:
    """A transaction exactly 24 hours before is inside the window; a second earlier is not."""
    rows: list[Row] = [
        ("C1", "W0", "2025-01-10 08:59:59", 1, "USD", 5, "México", "Food", None, False, 1),
        ("C1", "W1", "2025-01-10 09:00:00", 1, "USD", 10, "México", "Food", None, False, 1),
        ("C1", "W2", "2025-01-11 08:59:59", 1, "USD", 20, "México", "Food", None, False, 1),
        ("C1", "W3", "2025-01-11 09:00:00", 1, "USD", 30, "México", "Food", None, False, 1),
    ]
    silver = _write_silver(tmp_path / "s", rows)
    build_risk_features(silver, tmp_path / "g", SPLIT, code_version="test")

    mart = _mart(tmp_path / "g")

    assert (mart["W3"]["tx_count_24h"], mart["W3"]["tx_sum_usd_24h"]) == (2, 30.0)
    assert (mart["W2"]["tx_count_24h"], mart["W2"]["tx_sum_usd_24h"]) == (2, 15.0)
    assert (mart["W3"]["tx_count_7d"], mart["W3"]["tx_sum_usd_7d"]) == (3, 35.0)


def test_seconds_since_the_previous_transaction_use_strictly_earlier_time(built: Any) -> None:
    """The first transaction has none; a same-instant peer is not earlier; gaps are in seconds."""
    _, mart = built

    assert mart["T1"]["seconds_since_previous"] is None
    assert (
        mart["T4"]["seconds_since_previous"] is None
    )  # same instant as T1: nothing strictly earlier
    assert mart["T2"]["seconds_since_previous"] == 6 * 3600
    assert mart["T3"]["seconds_since_previous"] == 66 * 3600


def test_distance_is_measured_from_the_previous_located_transaction(built: Any) -> None:
    """T3 is in Guadalajara; the previous transaction with coordinates is T1 in Mexico City."""
    _, mart = built

    assert mart["T3"]["distance_previous_km"] == pytest.approx(
        _haversine(MEXICO_CITY, GUADALAJARA), abs=0.01
    )
    assert mart["T1"]["distance_previous_km"] is None  # nothing earlier
    assert mart["T2"]["distance_previous_km"] is None  # no coordinates of its own


# -----------------------------------------------------------------------------
# Periods
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ident", "period"),
    [("T6", "train"), ("T5", "validation"), ("T7", "validation"), ("T8", "test"), ("T1", "train")],
)
def test_the_last_moment_of_each_period_belongs_to_it_and_the_next_moment_to_the_next(
    built: Any, ident: str, period: str
) -> None:
    """2025-03-31 23:59:59 is training, midnight after is validation; the same at 2025-09-30."""
    _, mart = built

    assert mart[ident]["split"] == period


def test_the_manifest_counts_rows_positives_and_coverage_per_period(built: Any) -> None:
    """Rows and positives per period, and how many rows carry each feature."""
    gold, _ = built
    manifest = json.loads((gold / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["rows"] == {"train": 6, "validation": 2, "test": 1}
    assert manifest["positives"] == {"train": 1, "validation": 1, "test": 0}
    assert manifest["coverage"]["amount_usd"] == 8
    assert manifest["coverage"]["seconds_since_previous"] == 5
    assert manifest["coverage"]["customer_country"] == 8
    assert manifest["coverage"]["country_mismatch"] == 8
    assert manifest["coverage"]["distance_previous_km"] == 1
    assert manifest["split"] == {"train_end": "2025-03-31", "validation_end": "2025-09-30"}
    assert manifest["features"] == list(FEATURES)


# -----------------------------------------------------------------------------
# Leakage guards
# -----------------------------------------------------------------------------


def test_the_mart_holds_only_the_key_the_period_the_label_and_the_declared_features(
    built: Any,
) -> None:
    """No identifier of the customer and no excluded source column reaches the mart."""
    _, mart = built

    assert list(mart["T1"]) == ["transaction_id", "transaction_ts", "split", "is_fraud", *FEATURES]
    forbidden = {"customer_id", "product_id", "fraud_score", "response_code", "transaction_status"}
    assert forbidden.isdisjoint(mart["T1"])
    assert all(reason for reason in EXCLUDED.values())


def test_later_transactions_and_later_labels_never_change_earlier_rows(tmp_path: Path) -> None:
    """Append later activity: the features of every earlier row stay the same."""
    base_silver = _write_silver(tmp_path / "a", BASE)
    build_risk_features(base_silver, tmp_path / "ga", SPLIT, code_version="test")
    later: list[Row] = [
        *BASE,
        (
            "C1",
            "T9",
            "2025-01-14 10:00:00",
            5000,
            "USD",
            5000,
            "Spain",
            "Food",
            GUADALAJARA,
            True,
            99,
        ),
        ("C1", "T10", "2025-01-10 09:00:01", 999, "USD", 999, "USA", "Food", MEXICO_CITY, True, 99),
        ("C2", "T11", "2025-12-01 10:00:00", 1, "COP", 1, "Colombia", "Services", None, False, 1),
    ]
    later_silver = _write_silver(tmp_path / "b", later)
    build_risk_features(later_silver, tmp_path / "gb", SPLIT, code_version="test")

    before, after = _mart(tmp_path / "ga"), _mart(tmp_path / "gb")

    # T10 is an EARLIER-than-T2 transaction added late: it legitimately changes T2, T3 and T4's
    # history, so only the rows that come before every addition are compared
    for ident in ("T1", "T4", "T6", "T5", "T7", "T8"):
        assert before[ident] == after[ident], ident
    assert before["T3"]["tx_count_24h"] == after["T3"]["tx_count_24h"]


def test_flipping_the_label_of_a_later_transaction_changes_no_earlier_feature(
    tmp_path: Path,
) -> None:
    """Labels are known later: changing the label of T3 or T7 leaves every earlier row alone."""
    flipped = [
        (*row[:9], not row[9], row[10]) if row[1] in {"T3", "T7", "T8"} else row for row in BASE
    ]
    a = _write_silver(tmp_path / "a", BASE)
    b = _write_silver(tmp_path / "b", flipped)
    build_risk_features(a, tmp_path / "ga", SPLIT, code_version="test")
    build_risk_features(b, tmp_path / "gb", SPLIT, code_version="test")

    before, after = _mart(tmp_path / "ga"), _mart(tmp_path / "gb")

    for ident in before:
        features_before = {k: v for k, v in before[ident].items() if k != "is_fraud"}
        features_after = {k: v for k, v in after[ident].items() if k != "is_fraud"}
        assert features_before == features_after, ident


def test_the_fraud_score_is_excluded_with_evidence_of_why(built: Any) -> None:
    """The manifest records how strongly the score carries the label."""
    gold, _ = built
    evidence = json.loads((gold / "manifest.json").read_text(encoding="utf-8"))[
        "fraud_score_evidence"
    ]

    assert evidence == {
        "highest_score_when_not_fraud": 30.0,
        "fraud_rows": 2,
        "fraud_rows_above_that": 2,
    }


# -----------------------------------------------------------------------------
# Determinism, report and command line
# -----------------------------------------------------------------------------


def test_the_same_inputs_produce_byte_identical_outputs(tmp_path: Path) -> None:
    """Two builds give the same mart and manifest."""
    silver = _write_silver(tmp_path / "s", BASE)
    build_risk_features(silver, tmp_path / "g1", SPLIT, code_version="test")
    build_risk_features(silver, tmp_path / "g2", SPLIT, code_version="test")

    for name in ("risk_features.parquet", "manifest.json"):
        assert (tmp_path / "g1" / name).read_bytes() == (tmp_path / "g2" / name).read_bytes()
        assert hashlib.sha256((tmp_path / "g1" / name).read_bytes())


def test_a_missing_cleaned_table_stops_the_build(tmp_path: Path) -> None:
    """A partial cleaned layer is refused before anything is written."""
    with pytest.raises(FileNotFoundError, match="transactions"):
        build_risk_features(tmp_path / "none", tmp_path / "gold", SPLIT, code_version="test")

    assert not (tmp_path / "gold").exists()


def test_the_report_states_the_periods_the_exclusions_and_the_lineage(built: Any) -> None:
    """The report is a pure function of the manifest and names what it must."""
    gold, _ = built
    silver = gold.parent / "base" / "silver"
    manifest = build_risk_features(silver, gold, SPLIT, code_version="test")

    text = render_report(manifest)

    assert render_report(manifest) == text
    assert "| train | up to and including 2025-03-31 | 6 | 1 | 16.667 % |" in text
    assert "| validation | after 2025-03-31, through 2025-09-30 | 2 | 1 | 50.000 % |" in text
    assert "| test | after 2025-09-30 | 1 | 0 | 0.000 % |" in text
    for name in ("fraud_score", "response_code", "transaction_status"):
        assert f"`{name}`" in text
    assert "no transaction that is not fraud scores above 30.0, while 100.0 % of the fraud" in text
    assert "`distance_previous_km`" in text and "| `transaction_id`" not in text
    assert "2 of 3 customers were last updated after the training period ended" in text
    assert "62.5 % of the transactions belong to a customer whose record is newer" in text
    assert "## 5. Do the remaining features stand in for the excluded columns?" in text
    for label in PROXIES:
        assert (
            f"| {label} | Transactions | Fraud | Prevalence | Train | Validation | Test |" in text
        )
    assert "| `converted` | 1 | 0 | 0.000 % | 0.000 % | n/a | n/a |" in text
    assert "| `unavailable` | 1 | 1 | 100.000 % | 100.000 % | n/a | n/a |" in text


def test_the_command_writes_the_report_and_is_idempotent(tmp_path: Path) -> None:
    """End to end from a cleaned layer; a second run changes nothing."""
    silver = _write_silver(tmp_path / "s", BASE)
    split_file = tmp_path / "split.toml"
    split_file.write_text("train_end = 2025-03-31\nvalidation_end = 2025-09-30\n", encoding="utf-8")
    arguments = [
        "--silver",
        str(silver),
        "--gold",
        str(tmp_path / "gold"),
        "--report",
        str(tmp_path / "report.md"),
        "--split",
        str(split_file),
        "--code-version",
        "test",
    ]

    assert main(arguments) == 0
    first = (tmp_path / "report.md").read_bytes()
    assert main(arguments) == 0

    assert (tmp_path / "report.md").read_bytes() == first


def test_the_command_fails_with_one_on_a_missing_layer_or_an_invalid_split(tmp_path: Path) -> None:
    """Both failures exit 1 and write no report."""
    bad_split = tmp_path / "split.toml"
    bad_split.write_text("train_end = 2025-09-30\nvalidation_end = 2025-03-31\n", encoding="utf-8")
    common = ["--gold", str(tmp_path / "gold"), "--report", str(tmp_path / "report.md")]

    assert main(["--silver", str(tmp_path / "none"), *common]) == 1
    silver = _write_silver(tmp_path / "s", BASE)
    assert main(["--silver", str(silver), "--split", str(bad_split), *common]) == 1
    assert not (tmp_path / "report.md").exists()


# -----------------------------------------------------------------------------
# Split file
# -----------------------------------------------------------------------------


def test_the_shipped_split_loads_and_is_ordered() -> None:
    """The boundaries in the repository are valid and in order."""
    split = load_split(DEFAULT_SPLIT)

    assert split.train_end < split.validation_end


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("validation_end = 2025-09-30\n", "train_end"),
        ("train_end = 2025-03-31\n", "validation_end"),
        ('train_end = "2025-03-31"\nvalidation_end = 2025-09-30\n', "must be dates"),
        ("train_end = 2025-09-30\nvalidation_end = 2025-09-30\n", "before"),
        ("train_end = 2025-10-30\nvalidation_end = 2025-09-30\n", "before"),
    ],
    ids=["no-train", "no-validation", "text-date", "equal", "reversed"],
)
def test_an_invalid_split_is_refused(tmp_path: Path, content: str, message: str) -> None:
    """Missing, non-date or unordered boundaries stop the build."""
    path = tmp_path / "split.toml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_split(path)


# -----------------------------------------------------------------------------
# What the manifest says about the customer snapshot, the proxies and unavailable amounts
# -----------------------------------------------------------------------------


def test_the_manifest_measures_how_far_the_customer_snapshot_postdates_the_transactions(
    built: Any,
) -> None:
    """Two of three customers were updated after the training period (one exactly on its last day
    does not count); five of eight transactions with a customer are older than their record."""
    gold, _ = built
    snapshot = json.loads((gold / "manifest.json").read_text(encoding="utf-8"))["customer_snapshot"]

    assert snapshot == {
        "customers": 3,
        "customers_updated_after_train_end": 2,
        "transactions_with_a_customer": 8,
        "transactions_before_the_customer_snapshot": 5,
    }


def test_the_manifest_gives_the_prevalence_inputs_of_every_proxy_check(built: Any) -> None:
    """Rows and positives per value and period, so the report can show whether a feature stands in
    for an outcome column."""
    gold, _ = built
    proxies = json.loads((gold / "manifest.json").read_text(encoding="utf-8"))["proxies"]

    assert set(proxies) == set(PROXIES)
    assert proxies["amount_usd_source"]["reported"] == {
        "train": [4, 0],
        "validation": [2, 1],
        "test": [1, 0],
    }
    assert proxies["amount_usd_source"]["converted"] == {"train": [1, 0]}
    assert proxies["amount_usd_source"]["unavailable"] == {"train": [1, 1]}
    assert proxies["coordinates present"]["yes"] == {"train": [2, 1]}
    assert proxies["merchant category stated"]["no"]["train"] == [1, 0]


def test_an_unconvertible_amount_counts_in_a_window_but_adds_nothing_to_its_total(
    tmp_path: Path,
) -> None:
    """The count includes the transaction; the total treats its amount as zero (documented)."""
    rows: list[Row] = [
        ("C1", "U1", "2025-01-11 10:00:00", 100, "MXN", None, "México", "Food", None, False, 1),
        ("C1", "U2", "2025-01-11 11:00:00", 1, "USD", 10, "México", "Food", None, False, 1),
        ("C1", "U3", "2025-01-11 12:00:00", 1, "USD", 20, "México", "Food", None, False, 1),
    ]
    silver = _write_silver(tmp_path / "s", rows)
    build_risk_features(silver, tmp_path / "g", SPLIT, code_version="test")

    mart = _mart(tmp_path / "g")

    assert mart["U1"]["amount_usd_source"] == "unavailable"
    assert (mart["U2"]["tx_count_24h"], mart["U2"]["tx_sum_usd_24h"]) == (1, 0.0)
    assert (mart["U3"]["tx_count_24h"], mart["U3"]["tx_sum_usd_24h"]) == (2, 10.0)


def test_the_previous_located_transaction_is_the_latest_one_not_the_highest_identifier(
    tmp_path: Path,
) -> None:
    """Two earlier located transactions whose identifiers sort against their times."""
    rows: list[Row] = [
        ("C1", "Z9", "2025-01-11 08:00:00", 1, "USD", 1, "México", "Food", GUADALAJARA, False, 1),
        ("C1", "A1", "2025-01-11 09:00:00", 1, "USD", 1, "México", "Food", MEXICO_CITY, False, 1),
        ("C1", "M5", "2025-01-11 10:00:00", 1, "USD", 1, "México", "Food", GUADALAJARA, False, 1),
    ]
    silver = _write_silver(tmp_path / "s", rows)
    build_risk_features(silver, tmp_path / "g", SPLIT, code_version="test")

    mart = _mart(tmp_path / "g")

    assert mart["M5"]["distance_previous_km"] == pytest.approx(
        _haversine(MEXICO_CITY, GUADALAJARA), abs=0.01
    )
    assert mart["A1"]["distance_previous_km"] == pytest.approx(
        _haversine(GUADALAJARA, MEXICO_CITY), abs=0.01
    )
