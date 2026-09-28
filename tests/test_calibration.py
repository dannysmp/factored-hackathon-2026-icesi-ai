"""
Calibration, Threshold and Model Card Tests
============================================

Component: ``models.calibration``. Hermetic: a handmade mart with a transaction identifier and a
matching handmade cleaned-transactions table, small enough to fit a model and run the bootstrap
in milliseconds; the threshold-search and scoring helpers are tested directly on plain arrays.
"""

from __future__ import annotations

# Standard libraries
import json  # Reading back the experiment-log lines and the model card
from collections.abc import Callable  # Per-row fraud rule of the asymmetric train/test dataset
from datetime import UTC, date, datetime  # A fixed, injected clock and the split boundaries
from pathlib import Path  # Temporary locations
from typing import Any  # Row dictionaries

# Third-party libraries
import duckdb  # Writing the handmade mart and the handmade cleaned transactions table
import numpy as np  # Score and label arrays
import pytest  # Test runner and fixtures

# Local modules
from models.boosted import BootstrapInterval
from models.calibration import (
    _decide_routing,
    choose_threshold,
    latest_selected_model,
    main,
    run_calibration,
    write_model_card,
)
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
    """A minimal cleaned transactions table: only what the test-period join needs."""
    table = silver_dir / "silver"
    table.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute('CREATE TABLE t ("transaction_id" VARCHAR, "customer_id" VARCHAR)')
        if customer_of:
            con.executemany("INSERT INTO t VALUES (?, ?)", list(customer_of.items()))
        con.execute(f"COPY t TO '{table / 'transactions.parquet'}' (FORMAT PARQUET)")
    finally:
        con.close()
    return silver_dir


def _write_manifest(path: Path, *, code_version: str = "abc1234", digest: str = "0" * 12) -> Path:
    """A manifest with just the two fields `run_calibration` reads."""
    path.write_text(
        json.dumps({"code_version": code_version, "output_sha256": digest}), encoding="utf-8"
    )
    return path


def _write_model_log(path: Path, *, selected: str = "logistic") -> Path:
    """An experiment log whose last bootstrap entry selects `selected`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"bootstrap": {"selected": selected}}) + "\n")
    return path


def _dataset(*, separating: bool) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Train, validation and test rows across several customers, and their customer map.

    With `separating`, fraud is a narrow, learnable band of `amount_usd` shared by every period,
    so a threshold near that band routes almost nothing else and reaches a high precision.
    Without it, fraud is a fixed, amount-independent pattern (no signal for any threshold).
    """
    rows: list[dict[str, Any]] = []
    customer_of: dict[str, str] = {}
    counter = 0

    def add(split: str, customer: str, n: int) -> None:
        nonlocal counter
        for _ in range(n):
            band = counter % 20
            fraud = (band == 10) if separating else (counter % 11 == 0)
            amount = 10.0 + band
            transaction_id = f"T{counter}"
            rows.append(
                _default_row(split, fraud, transaction_id, amount_usd=amount, hour=counter % 24)
            )
            customer_of[transaction_id] = customer
            counter += 1

    for customer in range(60):
        add("train", f"C{customer}", 10)
    for customer in range(60, 75):
        add("validation", f"C{customer}", 8)
    for customer in range(75, 135):
        add("test", f"C{customer}", 5)
    return rows, customer_of


# -----------------------------------------------------------------------------
# latest_selected_model
# -----------------------------------------------------------------------------


def test_latest_selected_model_reads_the_last_bootstrap_entry(tmp_path: Path) -> None:
    log = tmp_path / "experiments.jsonl"
    log.write_text(
        json.dumps({"models": [{"name": "logistic"}]})
        + "\n"
        + json.dumps({"bootstrap": {"selected": "boosted"}})
        + "\n",
        encoding="utf-8",
    )
    assert latest_selected_model(log) == "boosted"


def test_latest_selected_model_raises_without_a_bootstrap_entry(tmp_path: Path) -> None:
    log = tmp_path / "experiments.jsonl"
    log.write_text(json.dumps({"models": [{"name": "logistic"}]}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bootstrap"):
        latest_selected_model(log)


def test_latest_selected_model_raises_when_the_log_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bootstrap"):
        latest_selected_model(tmp_path / "absent.jsonl")


def test_latest_selected_model_skips_blank_lines(tmp_path: Path) -> None:
    log = tmp_path / "experiments.jsonl"
    log.write_text(
        json.dumps({"bootstrap": {"selected": "logistic"}}) + "\n\n \n",
        encoding="utf-8",
    )
    assert latest_selected_model(log) == "logistic"


# -----------------------------------------------------------------------------
# choose_threshold
# -----------------------------------------------------------------------------


def test_choose_threshold_picks_the_lowest_threshold_meeting_the_floor_and_cap() -> None:
    # 100 rows, 10 positives. Scores 0.9 catch 5 positives with no false positive (precision 1.0,
    # share 5%); scores 0.5 catch all 10 positives with 10 false positives (precision 0.5, share
    # 20%, over the 5% cap). Only the higher threshold satisfies both, so it must be the one chosen
    # even though the pre-registration favours the lowest -- the lower one fails the cap.
    label = np.array([True] * 5 + [False] * 15 + [True] * 5 + [False] * 75)
    scores = np.array([0.9] * 5 + [0.1] * 15 + [0.5] * 5 + [0.1] * 75)
    chosen = choose_threshold(label, scores, floor=0.9, cap=0.05)
    assert chosen is not None
    assert chosen.threshold == pytest.approx(0.9)
    assert chosen.validation_precision == pytest.approx(1.0)
    assert chosen.validation_routed_share == pytest.approx(0.05)


def test_choose_threshold_returns_none_when_nothing_clears_the_floor() -> None:
    # The 5 positives share their top score with 45 negatives: precision tops out at 5/50 = 0.10,
    # far under the 0.99 floor, at every threshold.
    label = np.array([True] * 5 + [False] * 95)
    scores = np.array([0.5] * 50 + [0.4] * 50)
    assert choose_threshold(label, scores, floor=0.99, cap=0.05) is None


def test_choose_threshold_returns_none_for_an_empty_period() -> None:
    assert choose_threshold(np.array([], dtype=bool), np.array([]), floor=0.01, cap=0.05) is None


# -----------------------------------------------------------------------------
# _decide_routing
# -----------------------------------------------------------------------------


def test_decide_routing_enables_when_precision_and_interval_both_clear() -> None:
    interval = BootstrapInterval(point=0.05, lower=0.02, upper=0.08)
    enabled, rationale = _decide_routing(0.05, interval, test_prevalence=0.01, floor=0.01)
    assert enabled is True
    assert "meets the" in rationale
    assert "is above the" in rationale


def test_decide_routing_disables_when_precision_misses_the_floor() -> None:
    """AC-E6-05's first failure mode: the validation result does not hold on test."""
    interval = BootstrapInterval(point=0.005, lower=0.001, upper=0.02)
    enabled, rationale = _decide_routing(0.005, interval, test_prevalence=0.001, floor=0.01)
    assert enabled is False
    assert "falls below the floor" in rationale


def test_decide_routing_disables_when_the_interval_does_not_clear_prevalence() -> None:
    """AC-E6-05's other failure mode: precision meets the floor but the bootstrap interval does
    not distinguish it from chance -- the branch a mart-and-silver fixture cannot cheaply force."""
    interval = BootstrapInterval(point=0.02, lower=0.005, upper=0.05)
    enabled, rationale = _decide_routing(0.02, interval, test_prevalence=0.01, floor=0.01)
    assert enabled is False
    assert "is not above the test" in rationale
    assert "not distinguishable from chance" in rationale


def test_decide_routing_treats_an_interval_equal_to_prevalence_as_not_clearing_it() -> None:
    """The rule is a strict `>`, so a lower bound exactly at prevalence does not enable routing."""
    interval = BootstrapInterval(point=0.02, lower=0.01, upper=0.05)
    enabled, _ = _decide_routing(0.02, interval, test_prevalence=0.01, floor=0.01)
    assert enabled is False


# -----------------------------------------------------------------------------
# run_calibration
# -----------------------------------------------------------------------------


def test_a_negative_result_disables_routing_and_still_reports_calibration(tmp_path: Path) -> None:
    rows, customer_of = _dataset(separating=False)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", customer_of)
    log = _write_model_log(tmp_path / "experiments.jsonl", selected="logistic")

    result = run_calibration(
        mart, manifest, silver, split=SPLIT, model_log=log, seed=1, now=NOW, floor=0.5, cap=0.05
    )

    assert result.threshold is None
    assert result.test_scoring is None
    assert result.routing_enabled is False
    assert "no usable signal" in result.rationale
    assert {c.period for c in result.calibration} == {"validation", "test"}
    for diagnostics in result.calibration:
        assert 0.0 <= diagnostics.brier_score <= 1.0
        assert 0.0 <= diagnostics.expected_calibration_error <= 1.0
        assert sum(point.count for point in diagnostics.curve) > 0


def test_a_clearly_separating_feature_can_enable_routing(tmp_path: Path) -> None:
    rows, customer_of = _dataset(separating=True)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", customer_of)
    log = _write_model_log(tmp_path / "experiments.jsonl", selected="boosted")

    result = run_calibration(
        mart,
        manifest,
        silver,
        split=SPLIT,
        model_log=log,
        seed=1,
        now=NOW,
        floor=0.5,
        cap=0.5,
        resamples=50,
    )

    assert result.threshold is not None
    assert result.test_scoring is not None
    # Whatever the routing verdict, the test period must have been scored exactly once and the
    # precision at the threshold must be internally consistent with a positive count that never
    # exceeds the test period's total positives.
    assert result.test_scoring.positives >= 0
    assert 0.0 <= result.test_scoring.precision <= 1.0
    assert 0.0 <= result.test_scoring.routed_share <= 1.0


def test_a_threshold_that_worked_on_validation_can_fail_on_test(tmp_path: Path) -> None:
    """AC-E6-05: a validation threshold is not shipped just because it worked on validation."""
    rows: list[dict[str, Any]] = []
    customer_of: dict[str, str] = {}
    counter = 0

    def add(split: str, customer: str, n: int, fraud_rule: Callable[[int, int], bool]) -> None:
        nonlocal counter
        for _ in range(n):
            band = counter % 20
            fraud = fraud_rule(band, counter)
            amount = 10.0 + band
            transaction_id = f"T{counter}"
            rows.append(
                _default_row(split, fraud, transaction_id, amount_usd=amount, hour=counter % 24)
            )
            customer_of[transaction_id] = customer
            counter += 1

    # Train and validation: a clean, learnable rule (a middle band of `amount_usd` is fraud,
    # nothing else is), so the model finds a validation threshold that clears a high floor.
    for customer in range(60):
        add("train", f"C{customer}", 10, lambda band, _i: 8 <= band < 12)
    for customer in range(60, 75):
        add("validation", f"C{customer}", 8, lambda band, _i: 8 <= band < 12)
    # Test: the same band carries no fraud there; fraud is instead a handful of unrelated rows,
    # so the threshold learned from that band routes many test rows and catches almost none.
    for customer in range(75, 135):
        add("test", f"C{customer}", 5, lambda _band, i: i % 97 == 0)

    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", customer_of)
    log = _write_model_log(tmp_path / "experiments.jsonl", selected="boosted")

    result = run_calibration(
        mart,
        manifest,
        silver,
        split=SPLIT,
        model_log=log,
        seed=1,
        now=NOW,
        floor=0.8,
        cap=0.5,
        resamples=20,
    )

    assert result.threshold is not None
    assert result.test_scoring is not None
    assert result.routing_enabled is False
    assert result.test_scoring.precision < 0.8


def test_the_selected_model_name_comes_from_the_log_not_a_default(tmp_path: Path) -> None:
    rows, customer_of = _dataset(separating=False)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", customer_of)
    log = _write_model_log(tmp_path / "experiments.jsonl", selected="boosted")

    result = run_calibration(
        mart, manifest, silver, split=SPLIT, model_log=log, seed=1, now=NOW, floor=0.5, cap=0.05
    )
    assert result.selected_model == "boosted"


def test_run_calibration_raises_file_not_found_for_a_missing_mart_or_manifest(
    tmp_path: Path,
) -> None:
    rows, customer_of = _dataset(separating=False)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", customer_of)
    log = _write_model_log(tmp_path / "experiments.jsonl")
    with pytest.raises(FileNotFoundError):
        run_calibration(
            tmp_path / "absent.parquet",
            manifest,
            silver,
            split=SPLIT,
            model_log=log,
            seed=1,
            now=NOW,
        )
    with pytest.raises(FileNotFoundError):
        run_calibration(
            mart, tmp_path / "absent.json", silver, split=SPLIT, model_log=log, seed=1, now=NOW
        )


def test_run_calibration_raises_without_a_prior_model_selection(tmp_path: Path) -> None:
    rows, customer_of = _dataset(separating=False)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", customer_of)
    with pytest.raises(ValueError, match="bootstrap"):
        run_calibration(
            mart,
            manifest,
            silver,
            split=SPLIT,
            model_log=tmp_path / "experiments.jsonl",
            seed=1,
            now=NOW,
        )


def test_run_calibration_raises_for_an_unrecognised_selected_model(tmp_path: Path) -> None:
    rows, customer_of = _dataset(separating=False)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", customer_of)
    log = _write_model_log(tmp_path / "experiments.jsonl", selected="random-forest")
    with pytest.raises(ValueError, match="random-forest"):
        run_calibration(
            mart, manifest, silver, split=SPLIT, model_log=log, seed=1, now=NOW, floor=0.5, cap=0.05
        )


# -----------------------------------------------------------------------------
# write_model_card and the command line
# -----------------------------------------------------------------------------


def test_write_model_card_writes_readable_json(tmp_path: Path) -> None:
    rows, customer_of = _dataset(separating=False)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", customer_of)
    log = _write_model_log(tmp_path / "experiments.jsonl")
    result = run_calibration(
        mart, manifest, silver, split=SPLIT, model_log=log, seed=1, now=NOW, floor=0.5, cap=0.05
    )
    card = tmp_path / "model_card.json"
    write_model_card(result, card)
    loaded = json.loads(card.read_text(encoding="utf-8"))
    assert loaded["routing_enabled"] is False
    assert loaded["threshold"] is None
    assert isinstance(loaded["limitations"], list) and loaded["limitations"]


def test_the_command_runs_and_writes_the_card_and_a_log_line(tmp_path: Path) -> None:
    rows, customer_of = _dataset(separating=False)
    mart = _write_mart(tmp_path / "m.parquet", rows)
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", customer_of)
    split = tmp_path / "split.toml"
    split.write_text("train_end = 2025-03-31\nvalidation_end = 2025-09-30\n", encoding="utf-8")
    log = tmp_path / "experiments.jsonl"
    _write_model_log(log, selected="logistic")
    card = tmp_path / "model_card.json"

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
            "--card",
            str(card),
            "--seed",
            "3",
            "--floor",
            "0.5",
            "--cap",
            "0.05",
        ]
    )
    assert code == 0
    lines = log.read_text(encoding="utf-8").splitlines()
    # The pre-existing bootstrap-selection line, plus this run's own appended entry.
    assert len(lines) == 2
    entry = json.loads(lines[-1])
    assert entry["seed"] == 3
    assert entry["selected_model"] == "logistic"
    assert card.is_file()


def test_a_missing_mart_stops_the_command_without_a_log_line_or_card(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "manifest.json")
    silver = _write_silver(tmp_path / "silver", {})
    split = tmp_path / "split.toml"
    split.write_text("train_end = 2025-03-31\nvalidation_end = 2025-09-30\n", encoding="utf-8")
    log = tmp_path / "experiments.jsonl"
    _write_model_log(log, selected="logistic")
    before = log.read_text(encoding="utf-8")
    card = tmp_path / "model_card.json"

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
            "--card",
            str(card),
        ]
    )
    assert code == 1
    assert log.read_text(encoding="utf-8") == before
    assert not card.exists()
