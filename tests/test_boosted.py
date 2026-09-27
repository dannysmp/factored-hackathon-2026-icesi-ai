"""
Boosted Risk Model and Ablation Tests
======================================

Component: ``models.boosted``. Hermetic: a handmade mart with a transaction identifier and a
matching handmade cleaned-transactions table (transaction identifier to customer identifier only),
small enough to fit both models and run the bootstrap in milliseconds.
"""

from __future__ import annotations

# Standard libraries
import json  # Reading back the experiment-log lines
from datetime import UTC, date, datetime  # A fixed, injected clock and the split boundaries
from pathlib import Path  # Temporary locations
from typing import Any  # Row dictionaries

# Third-party libraries
import duckdb  # Writing the handmade mart and the handmade cleaned transactions table
import numpy as np  # Typing the fit_transform spy
import pytest  # Test runner and fixtures
from sklearn.compose import ColumnTransformer  # Spied on to prove the training-only fit

# Local modules
from models.boosted import (
    REDUCED_FEATURES,
    SNAPSHOT_FEATURES,
    bootstrap_test,
    main,
    run_ablation,
    run_boosted,
)
from models.probe import _column_types
from pipelines.risk_features import FEATURES, SplitConfig

NOW = datetime(2026, 9, 27, 0, 0, tzinfo=UTC)
SPLIT = SplitConfig(date(2025, 3, 31), date(2025, 9, 30))


def _default_row(
    split: str, is_fraud: bool, transaction_id: str, **overrides: Any
) -> dict[str, Any]:
    """Every mart column with a plain default, so a test states only what it varies."""
    row: dict[str, Any] = {
        "split": split,
        "is_fraud": is_fraud,
        "transaction_id": transaction_id,
        "amount_usd": 100.0,
        "amount_usd_source": "reported",
        "currency": "USD",
        "channel": "app",
        "transaction_type": "purchase",
        "merchant_category": "Food",
        "transaction_country": "México",
        "customer_country": "México",
        "country_mismatch": False,
        "hour": 12,
        "day_of_week": 3,
        "is_weekend": False,
        "tx_count_24h": 0,
        "tx_sum_usd_24h": 0.0,
        "tx_count_7d": 0,
        "tx_sum_usd_7d": 0.0,
        "seconds_since_previous": 3600,
        "distance_previous_km": 5.0,
    }
    row.update(overrides)
    return row


def _write_mart(path: Path, rows: list[dict[str, Any]]) -> Path:
    """Write `rows` as a mart with `transaction_id`, every `FEATURES` column and the label."""
    columns = ["transaction_id", "split", "is_fraud", *FEATURES]
    types = {
        "transaction_id": "VARCHAR",
        "split": "VARCHAR",
        "is_fraud": "BOOLEAN",
        "amount_usd": "DOUBLE",
        "amount_usd_source": "VARCHAR",
        "currency": "VARCHAR",
        "channel": "VARCHAR",
        "transaction_type": "VARCHAR",
        "merchant_category": "VARCHAR",
        "transaction_country": "VARCHAR",
        "customer_country": "VARCHAR",
        "country_mismatch": "BOOLEAN",
        "hour": "INTEGER",
        "day_of_week": "INTEGER",
        "is_weekend": "BOOLEAN",
        "tx_count_24h": "INTEGER",
        "tx_sum_usd_24h": "DOUBLE",
        "tx_count_7d": "INTEGER",
        "tx_sum_usd_7d": "DOUBLE",
        "seconds_since_previous": "BIGINT",
        "distance_previous_km": "DOUBLE",
    }
    definition = ", ".join(f'"{name}" {types[name]}' for name in columns)
    placeholders = ", ".join("?" for _ in columns)
    con = duckdb.connect()
    try:
        con.execute(f"CREATE TABLE mart ({definition})")
        con.executemany(
            f"INSERT INTO mart VALUES ({placeholders})",  # noqa: S608 -- `?` placeholders only
            [[row[name] for name in columns] for row in rows],
        )
        con.execute(f"COPY mart TO '{path}' (FORMAT PARQUET)")
    finally:
        con.close()
    return path


def _write_silver(silver_dir: Path, customer_of: dict[str, str]) -> Path:
    """A minimal cleaned transactions table: only what `bootstrap_test` joins on."""
    table = silver_dir / "silver"
    table.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute('CREATE TABLE t ("transaction_id" VARCHAR, "customer_id" VARCHAR)')
        if customer_of:
            con.executemany(
                "INSERT INTO t VALUES (?, ?)",
                list(customer_of.items()),
            )
        con.execute(f"COPY t TO '{table / 'transactions.parquet'}' (FORMAT PARQUET)")
    finally:
        con.close()
    return silver_dir


def _write_manifest(path: Path, *, code_version: str = "abc1234", digest: str = "0" * 12) -> Path:
    """A manifest with just the two fields `run_boosted` reads."""
    path.write_text(
        json.dumps({"code_version": code_version, "output_sha256": digest}), encoding="utf-8"
    )
    return path


def _dataset(*, separating: bool) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Train, validation and test rows across several customers, and their customer map.

    With `separating`, fraud is a *middle band* of `amount_usd` (neither the lowest nor the
    highest values): a pattern a single linear boundary cannot carve but a tree can, so the
    boosted model has a real, learnable edge over the logistic one. Without it, fraud is a fixed,
    amount-independent pattern (no signal for either model to find).
    """
    rows: list[dict[str, Any]] = []
    customer_of: dict[str, str] = {}
    counter = 0

    def add(split: str, customer: str, n: int) -> None:
        nonlocal counter
        for _ in range(n):
            band = counter % 20
            fraud = (8 <= band < 12) if separating else (counter % 11 == 0)
            # A bounded, cyclical domain shared by every period, so a model fitted on train sees
            # the same range of values test is later scored on.
            amount = 10.0 + band
            transaction_id = f"T{counter}"
            rows.append(
                _default_row(split, fraud, transaction_id, amount_usd=amount, hour=counter % 24)
            )
            customer_of[transaction_id] = customer
            counter += 1

    for customer in range(40):
        add("train", f"C{customer}", 10)
    for customer in range(40, 50):
        add("validation", f"C{customer}", 8)
    for customer in range(50, 110):
        add("test", f"C{customer}", 5)
    return rows, customer_of


def test_the_ablation_reports_both_feature_sets_and_both_models(tmp_path: Path) -> None:
    rows, _ = _dataset(separating=True)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, str(mart))
        tables = run_ablation(con, str(mart), column_types, seed=1)
    finally:
        con.close()
    assert {t.feature_set for t in tables} == {"full", "without_snapshot"}
    for table in tables:
        assert {m.name for m in table.models} == {"logistic", "boosted"}
    without_snapshot = next(t for t in tables if t.feature_set == "without_snapshot")
    assert without_snapshot.features == REDUCED_FEATURES
    assert set(SNAPSHOT_FEATURES).isdisjoint(without_snapshot.features)
    full = next(t for t in tables if t.feature_set == "full")
    assert set(SNAPSHOT_FEATURES).issubset(full.features)


def test_the_ablation_fits_the_preprocessor_on_training_rows_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows, _ = _dataset(separating=True)
    train_rows = sum(1 for row in rows if row["split"] == "train")
    mart = _write_mart(tmp_path / "m.parquet", rows)

    fit_row_counts: list[int] = []
    original_fit_transform = ColumnTransformer.fit_transform

    def spy(self: ColumnTransformer, x: np.ndarray, *args: Any, **kwargs: Any) -> np.ndarray:
        fit_row_counts.append(x.shape[0])
        return original_fit_transform(self, x, *args, **kwargs)  # type: ignore[no-any-return]

    monkeypatch.setattr(ColumnTransformer, "fit_transform", spy)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, str(mart))
        run_ablation(con, str(mart), column_types, seed=1)
    finally:
        con.close()
    # One call per feature set (full, without_snapshot), each fed exactly the training rows, never
    # the training rows plus the validation ones: a fit on the combined periods would leak the
    # validation distribution into the encoder and the imputer, and this would then see
    # train_rows + validation_rows for at least one call instead.
    assert fit_row_counts == [train_rows, train_rows]


def test_the_bootstrap_selects_boosted_when_a_feature_clearly_separates_the_label(
    tmp_path: Path,
) -> None:
    rows, customer_of = _dataset(separating=True)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    silver = _write_silver(tmp_path / "silver", customer_of)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, str(mart))
        result = bootstrap_test(con, str(mart), silver, column_types, seed=1, resamples=40)
    finally:
        con.close()
    assert result.selected == "boosted"
    assert result.difference.lower > 0


def test_the_bootstrap_selects_logistic_when_there_is_no_signal(tmp_path: Path) -> None:
    rows, customer_of = _dataset(separating=False)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    silver = _write_silver(tmp_path / "silver", customer_of)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, str(mart))
        result = bootstrap_test(con, str(mart), silver, column_types, seed=1, resamples=40)
    finally:
        con.close()
    assert result.selected == "logistic"
    assert "parsimony" in result.rationale


def test_the_bootstrap_never_reads_the_validation_period(tmp_path: Path) -> None:
    rows, customer_of = _dataset(separating=False)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, str(_write_mart(tmp_path / "a.parquet", rows)))
        baseline = bootstrap_test(
            con,
            str(_write_mart(tmp_path / "a.parquet", rows)),
            _write_silver(tmp_path / "silver_a", customer_of),
            column_types,
            seed=1,
            resamples=20,
        )
        extra_validation = [
            _default_row("validation", True, "TX", amount_usd=999_999.0, hour=1),
        ]
        customer_of_extra = {**customer_of, "TX": "C_extra"}
        changed = bootstrap_test(
            con,
            str(_write_mart(tmp_path / "b.parquet", rows + extra_validation)),
            _write_silver(tmp_path / "silver_b", customer_of_extra),
            column_types,
            seed=1,
            resamples=20,
        )
    finally:
        con.close()
    assert baseline == changed


def test_the_bootstrap_fits_the_preprocessor_on_training_rows_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows, customer_of = _dataset(separating=False)
    train_rows = sum(1 for row in rows if row["split"] == "train")
    mart = _write_mart(tmp_path / "m.parquet", rows)
    silver = _write_silver(tmp_path / "silver", customer_of)

    fit_row_counts: list[int] = []
    original_fit_transform = ColumnTransformer.fit_transform

    def spy(self: ColumnTransformer, x: np.ndarray, *args: Any, **kwargs: Any) -> np.ndarray:
        fit_row_counts.append(x.shape[0])
        return original_fit_transform(self, x, *args, **kwargs)  # type: ignore[no-any-return]

    monkeypatch.setattr(ColumnTransformer, "fit_transform", spy)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, str(mart))
        bootstrap_test(con, str(mart), silver, column_types, seed=1, resamples=5)
    finally:
        con.close()
    # Fed exactly the training rows, never the training rows plus the test ones: a fit on the
    # combined periods would leak the held-out test distribution into the encoder and the
    # imputer, and this assertion would then see train_rows + test_rows instead.
    assert fit_row_counts == [train_rows]


def test_the_customer_identifier_never_reaches_the_logged_result(tmp_path: Path) -> None:
    rows, customer_of = _dataset(separating=False)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", customer_of)
    result = run_boosted(mart, manifest, silver, split=SPLIT, seed=1, now=NOW)
    logged = json.dumps(result.as_dict())
    for customer in customer_of.values():
        assert customer not in logged


def test_run_boosted_raises_file_not_found_for_a_missing_mart_or_manifest(tmp_path: Path) -> None:
    rows, customer_of = _dataset(separating=False)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", customer_of)
    with pytest.raises(FileNotFoundError):
        run_boosted(tmp_path / "absent.parquet", manifest, silver, split=SPLIT, seed=1, now=NOW)
    with pytest.raises(FileNotFoundError):
        run_boosted(mart, tmp_path / "absent.json", silver, split=SPLIT, seed=1, now=NOW)


def test_a_missing_silver_transactions_table_raises(tmp_path: Path) -> None:
    rows, _ = _dataset(separating=False)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    with pytest.raises(duckdb.Error):
        run_boosted(mart, manifest, tmp_path / "no_such_silver", split=SPLIT, seed=1, now=NOW)


def test_the_command_runs_and_appends_one_log_line(tmp_path: Path) -> None:
    rows, customer_of = _dataset(separating=False)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", customer_of)
    split = tmp_path / "split.toml"
    split.write_text("train_end = 2025-03-31\nvalidation_end = 2025-09-30\n", encoding="utf-8")
    log = tmp_path / "experiments.jsonl"
    code = main(
        [
            "--mart",
            str(mart),
            "--manifest",
            str(manifest),
            "--silver",
            str(silver),
            "--split",
            str(split),
            "--log",
            str(log),
            "--seed",
            "3",
        ]
    )
    assert code == 0
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["seed"] == 3
    assert entry["bootstrap"]["selected"] in {"logistic", "boosted"}


def test_a_missing_mart_stops_the_command_without_a_log_line(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", {})
    split = tmp_path / "split.toml"
    split.write_text("train_end = 2025-03-31\nvalidation_end = 2025-09-30\n", encoding="utf-8")
    log = tmp_path / "experiments.jsonl"
    code = main(
        [
            "--mart",
            str(tmp_path / "absent.parquet"),
            "--manifest",
            str(manifest),
            "--silver",
            str(silver),
            "--split",
            str(split),
            "--log",
            str(log),
        ]
    )
    assert code == 1
    assert not log.exists()
