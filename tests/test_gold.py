"""
Dispute Demand Mart Tests
=========================

Component: ``pipelines.gold``. Hermetic: a small service dataset of known values
(``tests.data_fixture.build_service_dataset``) is cleaned into a temporary directory and the marts
are built from it, so every count, sum and quantile asserted here is derived by hand.
"""

from __future__ import annotations

# Standard libraries
import hashlib  # Compare mart files byte for byte
import json  # Read the manifest
from datetime import date  # Months of the marts
from pathlib import Path  # Temporary locations

# Third-party libraries
import pytest  # Test runner and fixtures

# Local modules
from pipelines.gold import MART_NAMES, build_marts, read_mart
from pipelines.silver import run_silver
from tests.data_fixture import build_service_dataset


@pytest.fixture
def silver(tmp_path: Path) -> Path:
    """A cleaned layer built from the service dataset."""
    raw = tmp_path / "raw"
    build_service_dataset(raw)
    out = tmp_path / "silver"
    run_silver(raw, out, code_version="test")
    return out


@pytest.fixture
def gold(tmp_path: Path, silver: Path) -> Path:
    """The marts built from that layer."""
    target = tmp_path / "gold"
    build_marts(silver, target, code_version="test")
    return target


def test_only_unrecognised_charge_complaints_of_the_transactions_category_are_disputes(
    gold: Path,
) -> None:
    """A complaint of another subcategory, or of another category, is not a dispute case."""
    monthly = read_mart(gold, "dispute_cases_monthly")

    assert [(row["month"], row["cases"]) for row in monthly] == [
        (date(2025, 1, 1), 3),
        (date(2025, 2, 1), 3),
    ]


def test_monthly_cases_count_outcomes_sla_repeat_and_first_response(gold: Path) -> None:
    """January has two resolved and one rejected case; February a closed and an escalated one."""
    january, february = read_mart(gold, "dispute_cases_monthly")

    assert (
        january["closed_cases"],
        january["rejected_cases"],
        january["sla_breached_cases"],
        january["repeat_complainer_cases"],
        january["first_response_cases"],
    ) == (2, 1, 1, 1, 3)
    assert (
        february["closed_cases"],
        february["escalated_cases"],
        february["first_response_cases"],
    ) == (1, 1, 2)


def test_resolution_statistics_use_only_resolved_and_closed_cases(gold: Path) -> None:
    """Days of 10, 20 and 30 give median 20 and P90 28; the 40 days of an escalated case are
    ignored by the overall figure but kept in its own status row."""
    (overall,) = read_mart(gold, "dispute_resolution_overall")
    by_status = {row["status"]: row for row in read_mart(gold, "dispute_resolution")}

    assert (overall["cases_with_days"], overall["median_days"], overall["p90_days"]) == (3, 20, 28)
    assert by_status["In Process"]["cases_with_days"] == 0
    assert by_status["Escalated"]["cases_with_days"] == 1
    assert (by_status["Resolved"]["median_days"], by_status["Resolved"]["p90_days"]) == (15, 19)


def test_claims_are_kept_per_currency_and_missing_currency_is_named(gold: Path) -> None:
    """Amounts are never pooled across currencies."""
    claims = {row["currency"]: row for row in read_mart(gold, "dispute_claims_by_currency")}

    assert claims["USD"]["claimed_total"] == 100
    assert claims["MXN"]["claimed_total"] == 200
    assert claims["ARS"]["claimed_total"] == 300
    assert (claims["unknown"]["cases"], claims["unknown"]["cases_with_amount"]) == (3, 0)


def test_contact_demand_carries_sums_that_recompose_the_means(gold: Path) -> None:
    """Two transactional contacts of 300 and 600 seconds: the mart holds count and sum."""
    rows = [
        row
        for row in read_mart(gold, "contact_demand_monthly")
        if row["reason_category"] == "Transaccional"
    ]

    assert sum(row["interactions"] for row in rows) == 2
    assert sum(row["duration_seconds_sum"] for row in rows) == 900
    assert sum(row["negative_interactions"] for row in rows) == 1
    assert sum(row["neutral_interactions"] for row in rows) == 1


def test_satisfaction_is_tied_to_the_reason_of_the_contact(gold: Path) -> None:
    """Surveys are joined to their contact, so each reason category gets its own score sum."""
    scores = {
        row["reason_category"]: (row["surveys"], row["score_sum"])
        for row in read_mart(gold, "contact_satisfaction")
    }

    assert scores == {"Queja": (1, 1), "Transaccional": (2, 8)}


def test_complaint_categories_count_every_complaint(gold: Path) -> None:
    """The category mix covers all eight complaints, disputes or not."""
    mix = read_mart(gold, "complaint_category_mix")

    assert sum(row["cases"] for row in mix) == 8


def test_marts_and_manifest_are_byte_identical_across_builds(
    tmp_path: Path, silver: Path, gold: Path
) -> None:
    """The same cleaned layer and code always produce the same files."""
    again = tmp_path / "again"

    build_marts(silver, again, code_version="test")

    def digests(directory: Path) -> dict[str, str]:
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.iterdir())
        }

    assert digests(gold) == digests(again)


def test_the_manifest_records_inputs_marts_and_definitions(gold: Path) -> None:
    """Lineage: digests of the cleaned inputs, rows and digests of each mart, and the definition."""
    manifest = json.loads((gold / "manifest.json").read_text(encoding="utf-8"))

    assert set(manifest["marts"]) == set(MART_NAMES)
    assert manifest["definitions"] == {
        "dispute_category": "Transactions",
        "dispute_subcategory": "Cargo no reconocido",
    }
    assert set(manifest["inputs"]) == {
        "complaints",
        "call_center_interactions",
        "satisfaction_surveys",
    }
    assert manifest["marts"]["dispute_cases_monthly"]["rows"] == 2


def test_a_missing_cleaned_table_stops_the_build_and_names_it(tmp_path: Path) -> None:
    """A partial cleaned layer is refused before anything is written."""
    with pytest.raises(FileNotFoundError, match="complaints"):
        build_marts(tmp_path / "empty", tmp_path / "gold", code_version="test")

    assert not (tmp_path / "gold").exists()


def test_reading_an_unknown_or_unbuilt_mart_fails_clearly(tmp_path: Path) -> None:
    """The reader distinguishes a wrong name from a mart that has not been built yet."""
    with pytest.raises(KeyError, match="nonsense"):
        read_mart(tmp_path, "nonsense")
    with pytest.raises(FileNotFoundError, match="dispute_cases_monthly"):
        read_mart(tmp_path, "dispute_cases_monthly")
