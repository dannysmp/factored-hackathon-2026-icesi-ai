"""
Case Customer Profile Tests
============================

Component: ``evals.profiles``. ``country_code`` and ``read_segments`` are hermetic (a temporary
Parquet file); ``load_case_profiles`` needs a real, migrated Postgres, marked ``integration`` and
skipped when ``DATABASE_URL`` is not set.
"""

from __future__ import annotations

# Standard libraries
import os
from pathlib import Path

# Third-party libraries
import duckdb
import psycopg
import pytest

# Local modules
from app.domain.policy.models import DisputeCategory
from app.persistence.migrate import apply_migrations
from contracts.service_v1.envelope import Intent
from evals.fairness import CaseProfile
from evals.models import Case, CaseCategory
from evals.profiles import country_code, load_case_profiles, read_segments


def _write_customers(silver_dir: Path) -> None:
    (silver_dir / "silver").mkdir(parents=True)
    path = silver_dir / "silver" / "customers.parquet"
    connection = duckdb.connect()
    connection.sql(
        "SELECT * FROM (VALUES ('CLI-A', 'Plus', '12345'), ('CLI-B', 'Basic', '67890'), "
        "('CLI-C', NULL, '11111')) AS t(customer_id, segment, document_number)"
    ).write_parquet(str(path))
    connection.close()


@pytest.mark.parametrize(
    ("stored", "code"),
    [("México", "MX"), ("Colombia", "CO"), ("Argentina", "AR"), ("Chile", "Chile")],
)
def test_country_code_maps_known_names_and_keeps_an_unknown_one(stored: str, code: str) -> None:
    assert country_code(stored) == code


def test_read_segments_returns_only_the_requested_customers(tmp_path: Path) -> None:
    _write_customers(tmp_path)

    assert read_segments(tmp_path, ["CLI-A", "CLI-Z"]) == {"CLI-A": "Plus"}


def test_read_segments_skips_a_customer_with_no_segment(tmp_path: Path) -> None:
    _write_customers(tmp_path)

    assert read_segments(tmp_path, ["CLI-B", "CLI-C"]) == {"CLI-B": "Basic"}


def test_read_segments_without_the_cleaned_table_returns_nothing(tmp_path: Path) -> None:
    assert read_segments(tmp_path, ["CLI-A"]) == {}


def test_read_segments_for_no_customers_returns_nothing(tmp_path: Path) -> None:
    _write_customers(tmp_path)

    assert read_segments(tmp_path, []) == {}


def _case(case_id: str, seed_ref: str) -> Case:
    return Case(
        case_id=case_id,
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="observed",
        seed_ref=seed_ref,
        user_turns=("hola",),
        expected_intent=Intent.CONFIRM_FILING,
        expected_category=DisputeCategory.UNRECOGNIZED_CHARGE,
    )


@pytest.fixture
def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(value)
    with psycopg.connect(value) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute("TRUNCATE TABLE cases, transactions, products, customers, audit_log CASCADE")
        conn.commit()
    with psycopg.connect(value, autocommit=True) as conn, conn.cursor() as cur:
        for customer_id, country in (("CLI-A", "México"), ("CLI-B", "Colombia")):
            cur.execute(
                "INSERT INTO customers (customer_id, first_name, last_name, country, "
                "customer_status) VALUES (%s, 'First', 'Last', %s, 'Active')",
                (customer_id, country),
            )
    return value


@pytest.mark.integration
def test_load_case_profiles_joins_the_store_country_and_the_cleaned_segment(
    dsn: str, tmp_path: Path
) -> None:
    _write_customers(tmp_path)
    cases = [
        _case("c-a", "ops_seed:CLI-A"),
        _case("c-b", "ops_seed:CLI-B"),
        _case("c-none", "ops_seed:CLI-GONE"),
    ]

    profiles = load_case_profiles(dsn, cases, silver_dir=tmp_path)

    assert profiles["c-a"] == CaseProfile(country="MX", segment="Plus")
    assert profiles["c-b"] == CaseProfile(country="CO", segment="Basic")
    assert profiles["c-none"] == CaseProfile(country=None, segment=None)


@pytest.mark.integration
def test_load_case_profiles_skips_a_case_whose_transaction_is_absent(
    dsn: str, tmp_path: Path
) -> None:
    profiles = load_case_profiles(
        dsn, [_case("c-x", "ops_seed:TRX-DOES-NOT-EXIST")], silver_dir=tmp_path
    )

    assert profiles == {}
