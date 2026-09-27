"""
Risk Signal Tabulation Tests
============================

Component: ``pipelines.risk_signal``. Hermetic: a handmade mart of a few rows whose bands are
counted by hand; the leakage tests change the test period and show the tabulation does not move.
"""

from __future__ import annotations

# Standard libraries
from pathlib import Path  # Temporary locations
from typing import Any  # Rows as tuples

# Third-party libraries
import duckdb  # Writing the handmade mart

# Local modules
from pipelines.risk_signal import (
    _AMOUNT_EDGES,
    _BAND_QUERIES,
    main,
    render_report,
    tabulate_signal,
)

# split, is_fraud, amount_usd, hour, tx_count_24h, tx_count_7d, seconds_since_previous
Row = tuple[str, bool, float | None, int, int, int, int | None]

TRAIN: list[Row] = [
    ("train", False, 10.0, 0, 0, 0, None),
    ("train", False, 20.0, 1, 0, 1, 90000),
    ("train", True, 30.0, 1, 1, 2, 4000),
    ("train", False, 40.0, 23, 2, 4, 100),
    ("train", False, 50.0, 23, 3, 7, 700000),
]
VALIDATION: list[Row] = [
    ("validation", True, 15.0, 1, 1, 1, 3600),
    ("validation", False, None, 0, 4, 9, 86400),
]
TEST: list[Row] = [("test", True, 99999.0, 5, 9, 9, 1), ("test", False, 1.0, 6, 0, 0, None)]


def _write_mart(path: Path, rows: list[Row]) -> Path:
    """Write ``rows`` as a mart with the columns the tabulation reads."""
    con = duckdb.connect()
    try:
        con.execute(
            "CREATE TABLE mart (split VARCHAR, is_fraud BOOLEAN, amount_usd DOUBLE, hour INTEGER, "
            "tx_count_24h INTEGER, tx_count_7d INTEGER, seconds_since_previous BIGINT)"
        )
        con.executemany("INSERT INTO mart VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        con.execute(f"COPY mart TO '{path}' (FORMAT PARQUET)")
    finally:
        con.close()
    return path


def _bands(mart: Path, feature: str) -> dict[str, Any]:
    """The counts of ``feature`` keyed by band label."""
    table = next(t for t in tabulate_signal(mart).tables if t.feature == feature)
    return {band.label: band.counts for band in table.bands}


def test_hours_are_counted_per_period_and_the_test_period_is_absent(tmp_path: Path) -> None:
    mart = _write_mart(tmp_path / "m.parquet", TRAIN + VALIDATION + TEST)
    hours = _bands(mart, "hour")
    assert len(hours) == 24
    assert hours["0"] == {"train": (1, 0), "validation": (1, 0)}
    assert hours["1"] == {"train": (2, 1), "validation": (1, 1)}
    assert hours["23"] == {"train": (2, 0), "validation": (0, 0)}
    assert hours["5"] == {"train": (0, 0), "validation": (0, 0)}  # only the test period has hour 5


def test_velocity_bands_use_fixed_edges_and_keep_the_first_transaction_apart(
    tmp_path: Path,
) -> None:
    mart = _write_mart(tmp_path / "m.parquet", TRAIN + VALIDATION)
    count_24h = _bands(mart, "tx_count_24h")
    assert count_24h["below 1"] == {"train": (2, 0), "validation": (0, 0)}
    assert count_24h["1 to below 2"] == {"train": (1, 1), "validation": (1, 1)}
    assert count_24h["2 to below 3"] == {"train": (1, 0), "validation": (0, 0)}
    assert count_24h["3 or more"] == {"train": (1, 0), "validation": (1, 0)}
    count_7d = _bands(mart, "tx_count_7d")
    assert count_7d["6 or more"] == {"train": (1, 0), "validation": (1, 0)}
    gaps = _bands(mart, "seconds_since_previous")
    assert gaps["no earlier transaction"] == {"train": (1, 0), "validation": (0, 0)}
    assert gaps["below 3,600"] == {"train": (1, 0), "validation": (0, 0)}
    assert gaps["3,600 to below 86,400"] == {"train": (1, 1), "validation": (1, 1)}
    assert gaps["86,400 to below 604,800"] == {"train": (1, 0), "validation": (1, 0)}
    assert gaps["604,800 or more"] == {"train": (1, 0), "validation": (0, 0)}


def test_amount_bands_come_from_the_training_period_and_hold_the_empty_amount(
    tmp_path: Path,
) -> None:
    mart = _write_mart(tmp_path / "m.parquet", TRAIN + VALIDATION)
    table = next(t for t in tabulate_signal(mart).tables if t.feature == "amount_usd")
    total = {
        period: (
            sum(band.counts[period][0] for band in table.bands),
            sum(band.counts[period][1] for band in table.bands),
        )
        for period in ("train", "validation")
    }
    assert total == {"train": (5, 1), "validation": (2, 1)}
    assert table.bands[-1].label == "unavailable"
    assert table.bands[-1].counts == {"train": (0, 0), "validation": (1, 0)}
    # The training decile edges of 10..50 are 14, 18, 22, 26, 30, 34, 38, 42, 46
    assert table.bands[0].label == "below 14"
    assert table.bands[0].counts["validation"] == (0, 0)
    assert table.bands[1].label == "14 to below 18"
    assert table.bands[1].counts["validation"] == (1, 1)  # the validation amount 15


def test_the_test_period_never_changes_the_tabulation(tmp_path: Path) -> None:
    baseline = tabulate_signal(_write_mart(tmp_path / "a.parquet", TRAIN + VALIDATION))
    with_test = tabulate_signal(_write_mart(tmp_path / "b.parquet", TRAIN + VALIDATION + TEST))
    flipped = [(s, not f, a, h, c, w, g) for s, f, a, h, c, w, g in TEST]
    flipped_test = tabulate_signal(
        _write_mart(tmp_path / "c.parquet", TRAIN + VALIDATION + flipped)
    )
    assert baseline == with_test == flipped_test


def test_a_mart_without_training_rows_falls_back_to_one_amount_band(tmp_path: Path) -> None:
    mart = _write_mart(tmp_path / "m.parquet", VALIDATION)
    amounts = _bands(mart, "amount_usd")
    assert amounts["all values"] == {"train": (0, 0), "validation": (1, 1)}
    assert amounts["unavailable"] == {"train": (0, 0), "validation": (1, 0)}


def test_the_report_shows_every_feature_with_its_counts(tmp_path: Path) -> None:
    mart = _write_mart(tmp_path / "m.parquet", TRAIN + VALIDATION)
    text = render_report(tabulate_signal(mart))
    for feature in ("amount_usd", "hour", "tx_count_24h", "tx_count_7d", "seconds_since_previous"):
        assert f"## {feature}" in text
    assert "| train | 5 | 1 | 20.000 % |" in text
    assert "| validation | 2 | 1 | 50.000 % |" in text
    assert "The test period is not read" in text
    assert "| 1 | 2 | 1 | 50.000 % | 1 | 1 | 100.000 % |" in text  # hour 1
    assert "| 5 | 0 | 0 | n/a | 0 | 0 | n/a |" in text  # an empty band has no prevalence


def test_the_command_writes_the_report(tmp_path: Path) -> None:
    mart = _write_mart(tmp_path / "m.parquet", TRAIN + VALIDATION)
    report = tmp_path / "out" / "signal.md"
    assert main(["--mart", str(mart), "--report", str(report)]) == 0
    assert report.read_text(encoding="utf-8").startswith("# Risk signal tabulation")
    assert not report.with_suffix(".md.tmp").exists()


def test_a_missing_mart_stops_the_command_without_a_report(tmp_path: Path) -> None:
    report = tmp_path / "signal.md"
    assert main(["--mart", str(tmp_path / "absent.parquet"), "--report", str(report)]) == 1
    assert not report.exists()


def test_a_mart_without_the_columns_stops_the_command(tmp_path: Path) -> None:
    broken = tmp_path / "broken.parquet"
    con = duckdb.connect()
    try:
        con.execute(f"COPY (SELECT 1 AS other) TO '{broken}' (FORMAT PARQUET)")
    finally:
        con.close()
    report = tmp_path / "signal.md"
    assert main(["--mart", str(broken), "--report", str(report)]) == 1
    assert not report.exists()


def test_every_query_reads_only_the_training_and_validation_periods() -> None:
    assert "split = 'train'" in _AMOUNT_EDGES
    for feature, query in _BAND_QUERIES.items():
        assert "split IN ('train', 'validation')" in query, feature


def test_the_amount_bands_are_the_nine_training_deciles(tmp_path: Path) -> None:
    mart = _write_mart(tmp_path / "m.parquet", TRAIN)
    table = next(t for t in tabulate_signal(mart).tables if t.feature == "amount_usd")
    assert [band.label for band in table.bands] == [
        "below 14",
        "14 to below 18",
        "18 to below 22",
        "22 to below 26",
        "26 to below 30",
        "30 to below 34",
        "34 to below 38",
        "38 to below 42",
        "42 to below 46",
        "46 or more",
    ]


def test_an_amount_equal_to_an_edge_falls_in_the_upper_band(tmp_path: Path) -> None:
    rows = [*TRAIN, ("validation", True, 14.0, 0, 0, 0, None)]
    amounts = _bands(_write_mart(tmp_path / "m.parquet", rows), "amount_usd")
    assert amounts["14 to below 18"]["validation"] == (1, 1)
    assert amounts["below 14"]["validation"] == (0, 0)


def test_tied_training_amounts_collapse_to_distinct_bands(tmp_path: Path) -> None:
    rows: list[Row] = [("train", False, 5.0, 0, 0, 0, None)] * 3
    table = next(
        t
        for t in tabulate_signal(_write_mart(tmp_path / "m.parquet", rows)).tables
        if t.feature == "amount_usd"
    )
    assert [band.label for band in table.bands] == ["below 5", "5 or more"]
    assert table.bands[1].counts["train"] == (3, 0)


def test_edges_that_round_to_the_same_cent_make_one_band(tmp_path: Path) -> None:
    rows: list[Row] = [("train", False, 10.0 + i * 0.0001, 0, 0, 0, None) for i in range(10)]
    table = next(
        t
        for t in tabulate_signal(_write_mart(tmp_path / "m.parquet", rows)).tables
        if t.feature == "amount_usd"
    )
    labels = [band.label for band in table.bands]
    assert len(labels) == len(set(labels))


def test_an_hour_outside_the_day_is_unknown_not_the_last_hour(tmp_path: Path) -> None:
    rows: list[Row] = [*TRAIN, ("train", True, 12.0, 25, 0, 0, None)]
    hours = _bands(_write_mart(tmp_path / "m.parquet", rows), "hour")
    assert hours["23"]["train"] == (2, 0)
    assert hours["unknown"]["train"] == (1, 1)
