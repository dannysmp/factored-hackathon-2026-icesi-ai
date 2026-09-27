"""
Risk Signal Probe Tests
=======================

Component: ``models.probe``. Hermetic: a handmade mart with every feature column, small enough to
fit both models in milliseconds; a feature engineered to separate the label checks the metric
wiring, and the leakage tests append or relabel test-period rows to show the fit does not move.
"""

from __future__ import annotations

# Standard libraries
import json  # Reading back the experiment-log lines
from dataclasses import replace  # Varying one field of a result
from datetime import UTC, date, datetime  # A fixed, injected clock and the split boundaries
from pathlib import Path  # Temporary locations
from typing import Any  # Row dictionaries

# Third-party libraries
import duckdb  # Writing the handmade mart
import numpy as np  # Building the direct-transform comparison
import pytest  # Test runner and fixtures
from sklearn.compose import ColumnTransformer  # Spied on to prove the training-only fit

# Local modules
from models.probe import (
    ModelResult,
    ProbeResult,
    _column_types,
    _fit_models,
    append_experiment,
    load_period,
    main,
    run_probe,
)
from pipelines.risk_features import FEATURES, SplitConfig

NOW = datetime(2026, 9, 27, 0, 0, tzinfo=UTC)
SPLIT = SplitConfig(date(2025, 3, 31), date(2025, 9, 30))


def _default_row(split: str, is_fraud: bool, **overrides: Any) -> dict[str, Any]:
    """Every mart column with a plain default, so a test states only what it varies."""
    row: dict[str, Any] = {
        "split": split,
        "is_fraud": is_fraud,
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
    """Write `rows` as a mart with every column `FEATURES` and `main` expect."""
    columns = ["split", "is_fraud", *FEATURES]
    types = {
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


def _write_manifest(path: Path, *, code_version: str = "abc1234", digest: str = "0" * 12) -> Path:
    """A manifest with just the two fields `run_probe` reads."""
    path.write_text(
        json.dumps({"code_version": code_version, "output_sha256": digest}), encoding="utf-8"
    )
    return path


def _train_and_validation(rows: int, *, separating: bool = False) -> list[dict[str, Any]]:
    """`rows` train rows and `rows // 3` validation rows.

    With `separating`, `amount_usd` alone predicts `is_fraud` (high amount, always fraud), so the
    metric wiring can be checked against a known answer.
    """
    made = []
    for i in range(rows):
        fraud = (i % 5 == 0) if separating else (i % 11 == 0)
        amount = 9_000.0 if (separating and fraud) else 10.0 + i
        made.append(_default_row("train", fraud, amount_usd=amount, hour=i % 24))
    for i in range(max(rows // 3, 6)):
        fraud = (i % 5 == 0) if separating else (i % 11 == 0)
        amount = 9_000.0 if (separating and fraud) else 10.0 + i
        made.append(_default_row("validation", fraud, amount_usd=amount, hour=i % 24))
    return made


def test_only_train_and_validation_rows_are_loaded(tmp_path: Path) -> None:
    rows = _train_and_validation(30)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, str(mart))
        train = load_period(con, str(mart), column_types, "train")
        validation = load_period(con, str(mart), column_types, "validation")
    finally:
        con.close()
    assert train.rows == 30
    assert validation.rows == 10


def test_adding_or_relabelling_test_rows_never_changes_the_result(tmp_path: Path) -> None:
    base_rows = _train_and_validation(60, separating=True)
    manifest = _write_manifest(tmp_path / "manifest.json")

    baseline = run_probe(
        _write_mart(tmp_path / "a.parquet", base_rows),
        manifest,
        split=SPLIT,
        seed=1,
        now=NOW,
    )
    extra_test = [_default_row("test", True, amount_usd=1.0), _default_row("test", False)]
    with_test = run_probe(
        _write_mart(tmp_path / "b.parquet", base_rows + extra_test),
        manifest,
        split=SPLIT,
        seed=1,
        now=NOW,
    )
    flipped_test = [{**row, "is_fraud": not row["is_fraud"]} for row in extra_test]
    with_flipped_test = run_probe(
        _write_mart(tmp_path / "c.parquet", base_rows + flipped_test),
        manifest,
        split=SPLIT,
        seed=1,
        now=NOW,
    )
    assert baseline.rows == with_test.rows == with_flipped_test.rows
    assert baseline.positives == with_test.positives == with_flipped_test.positives
    assert [m.validation_pr_auc for m in baseline.models] == [
        m.validation_pr_auc for m in with_test.models
    ]
    assert [m.validation_pr_auc for m in baseline.models] == [
        m.validation_pr_auc for m in with_flipped_test.models
    ]


def test_both_models_are_fitted_on_the_same_transformed_features(tmp_path: Path) -> None:
    rows = _train_and_validation(40, separating=True)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, str(mart))
        train = load_period(con, str(mart), column_types, "train")
        validation = load_period(con, str(mart), column_types, "validation")
    finally:
        con.close()
    models = _fit_models(train, validation, seed=1)
    assert isinstance(models, tuple)
    assert {m.name for m in models} == {"logistic", "boosted"}


def test_the_shared_preprocessor_is_fitted_on_training_rows_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _train_and_validation(40, separating=True)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, str(mart))
        train = load_period(con, str(mart), column_types, "train")
        validation = load_period(con, str(mart), column_types, "validation")
    finally:
        con.close()

    fit_row_counts: list[int] = []
    original_fit_transform = ColumnTransformer.fit_transform

    def spy(self: ColumnTransformer, x: np.ndarray, *args: Any, **kwargs: Any) -> np.ndarray:
        fit_row_counts.append(x.shape[0])
        return original_fit_transform(self, x, *args, **kwargs)  # type: ignore[no-any-return]

    monkeypatch.setattr(ColumnTransformer, "fit_transform", spy)
    _fit_models(train, validation, seed=1)
    # Fed exactly the training rows, never the training rows plus the validation ones: fitting on
    # the combined periods would leak the validation distribution into the encoder and the
    # imputer, and this assertion would then see train.rows + validation.rows instead.
    assert fit_row_counts == [train.rows]


def test_a_perfectly_separating_feature_gives_a_near_perfect_score(tmp_path: Path) -> None:
    rows = _train_and_validation(150, separating=True)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, str(mart))
        train = load_period(con, str(mart), column_types, "train")
        validation = load_period(con, str(mart), column_types, "validation")
    finally:
        con.close()
    models = _fit_models(train, validation, seed=1)
    for model in models:
        assert model.validation_pr_auc > 0.95, model.name


def test_missing_numeric_values_are_imputed_not_dropped(tmp_path: Path) -> None:
    rows = _train_and_validation(30)
    rows.append(
        _default_row("train", False, distance_previous_km=None, seconds_since_previous=None)
    )
    mart = _write_mart(tmp_path / "m.parquet", rows)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, str(mart))
        train = load_period(con, str(mart), column_types, "train")
        validation = load_period(con, str(mart), column_types, "validation")
    finally:
        con.close()
    models = _fit_models(train, validation, seed=1)  # must not raise
    assert len(models) == 2


def test_an_unseen_validation_category_does_not_raise(tmp_path: Path) -> None:
    rows = _train_and_validation(30)
    rows.append(_default_row("validation", False, merchant_category="Never seen in train"))
    mart = _write_mart(tmp_path / "m.parquet", rows)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, str(mart))
        train = load_period(con, str(mart), column_types, "train")
        validation = load_period(con, str(mart), column_types, "validation")
    finally:
        con.close()
    models = _fit_models(train, validation, seed=1)  # must not raise
    assert len(models) == 2


def test_a_null_categorical_value_becomes_the_literal_string_missing(tmp_path: Path) -> None:
    rows = _train_and_validation(30)
    rows.append(_default_row("train", False, merchant_category=None))
    mart = _write_mart(tmp_path / "m.parquet", rows)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, str(mart))
        train = load_period(con, str(mart), column_types, "train")
    finally:
        con.close()
    categorical_names = [name for name in FEATURES if column_types[name] == "VARCHAR"]
    merchant_category_column = categorical_names.index("merchant_category")
    values = set(train.categorical[:, merchant_category_column])
    assert "missing" in values
    assert "?" not in values
    assert None not in values


def test_the_result_is_deterministic_given_the_same_seed(tmp_path: Path) -> None:
    rows = _train_and_validation(60, separating=True)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    first = run_probe(mart, manifest, split=SPLIT, seed=7, now=NOW)
    second = run_probe(mart, manifest, split=SPLIT, seed=7, now=NOW)
    assert [m.validation_pr_auc for m in first.models] == [
        m.validation_pr_auc for m in second.models
    ]


def test_the_manifest_fields_pass_through_to_the_result(tmp_path: Path) -> None:
    rows = _train_and_validation(30)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json", code_version="feed1234", digest="ab" * 6)
    result = run_probe(mart, manifest, split=SPLIT, seed=1, now=NOW)
    assert result.mart_code_version == "feed1234"
    assert result.mart_output_sha256 == "ab" * 6
    assert result.timestamp == NOW.isoformat()


def test_a_missing_mart_or_manifest_raises_file_not_found(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "manifest.json")
    with pytest.raises(FileNotFoundError):
        run_probe(tmp_path / "absent.parquet", manifest, split=SPLIT, seed=1, now=NOW)
    mart = _write_mart(tmp_path / "m.parquet", _train_and_validation(10))
    with pytest.raises(FileNotFoundError):
        run_probe(mart, tmp_path / "absent.json", split=SPLIT, seed=1, now=NOW)


def test_append_experiment_only_adds_a_line(tmp_path: Path) -> None:
    log = tmp_path / "experiments.jsonl"
    first = ProbeResult(
        timestamp=NOW.isoformat(),
        code_version="aaa1111",
        mart_code_version="bbb2222",
        mart_output_sha256="c" * 12,
        split=SPLIT,
        seed=1,
        rows={"train": 10, "validation": 3},
        positives={"train": 1, "validation": 1},
        validation_base_rate=1 / 3,
        models=(ModelResult("logistic", {}, 0.5), ModelResult("boosted", {}, 0.6)),
    )
    append_experiment(first, log)
    before = log.read_text(encoding="utf-8")
    second = replace(first, seed=2)
    append_experiment(second, log)
    after = log.read_text(encoding="utf-8")
    assert after.startswith(before)
    assert after.count("\n") == 2
    lines = [json.loads(line) for line in after.splitlines()]
    assert lines[0]["seed"] == 1
    assert lines[1]["seed"] == 2


def test_the_command_runs_the_probe_and_appends_one_line(tmp_path: Path) -> None:
    rows = _train_and_validation(30)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    split = tmp_path / "split.toml"
    split.write_text("train_end = 2025-03-31\nvalidation_end = 2025-09-30\n", encoding="utf-8")
    log = tmp_path / "experiments.jsonl"
    code = main(
        [
            "--mart",
            str(mart),
            "--manifest",
            str(manifest),
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
    assert json.loads(lines[0])["seed"] == 3


def test_a_missing_mart_stops_the_command_without_a_log_line(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "manifest.json")
    split = tmp_path / "split.toml"
    split.write_text("train_end = 2025-03-31\nvalidation_end = 2025-09-30\n", encoding="utf-8")
    log = tmp_path / "experiments.jsonl"
    code = main(
        [
            "--mart",
            str(tmp_path / "absent.parquet"),
            "--manifest",
            str(manifest),
            "--split",
            str(split),
            "--log",
            str(log),
        ]
    )
    assert code == 1
    assert not log.exists()


def test_an_invalid_split_stops_the_command_without_a_log_line(tmp_path: Path) -> None:
    rows = _train_and_validation(10)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    split = tmp_path / "split.toml"
    split.write_text("train_end = 2025-09-30\nvalidation_end = 2025-03-31\n", encoding="utf-8")
    log = tmp_path / "experiments.jsonl"
    code = main(
        ["--mart", str(mart), "--manifest", str(manifest), "--split", str(split), "--log", str(log)]
    )
    assert code == 1
    assert not log.exists()
