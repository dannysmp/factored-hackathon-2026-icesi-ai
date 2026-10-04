"""
Analytics Mart Load Tests
=========================

Component: ``pipelines.analytics_load``. The manifest check and the checksum function are pure
and hermetic. Loading the marts into Postgres for real needs a migrated database: those tests are
marked ``integration`` and read the DSN from ``DATABASE_URL``, skipped when it is not set.
"""

from __future__ import annotations

# Standard libraries
import hashlib
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

# Third-party libraries
import psycopg
import pytest

# Local modules
import pipelines.analytics_load as analytics_load_module
from app.persistence.migrate import apply_migrations
from pipelines.analytics_load import (
    LoadResult,
    MartParity,
    _read_mart,
    _row_checksum,
    _verify_output_digests,
    load_marts,
)
from pipelines.gold import MANIFEST_NAME, MART_NAMES, build_marts
from pipelines.silver import run_silver
from tests.data_fixture import build_service_dataset

# -----------------------------------------------------------------------------
# _row_checksum
# -----------------------------------------------------------------------------


def test_row_checksum_is_deterministic() -> None:
    rows = [("a", 1), ("b", 2)]
    assert _row_checksum(rows) == _row_checksum(rows)


def test_row_checksum_is_order_independent() -> None:
    """A plain SELECT gives Postgres no ordering guarantee; the digest must not depend on it."""
    forward = [("a", 1), ("b", 2), ("c", 3)]
    shuffled = [("c", 3), ("a", 1), ("b", 2)]
    assert _row_checksum(forward) == _row_checksum(shuffled)


def test_row_checksum_differs_for_different_content() -> None:
    assert _row_checksum([("a", 1)]) != _row_checksum([("a", 2)])


def test_row_checksum_differs_when_a_duplicate_row_count_changes() -> None:
    """Order-independence must still distinguish a multiset from a set: two copies of a row
    hash differently from one, even though sorting alone cannot tell that apart by position."""
    assert _row_checksum([("a", 1)]) != _row_checksum([("a", 1), ("a", 1)])


def test_row_checksum_canonicalizes_date_and_decimal() -> None:
    """A `date`/`Decimal` row and the equivalent hand-written string row hash identically, since
    DuckDB and psycopg must agree on the digest regardless of which native type they returned."""
    native = [(date(2026, 1, 1), Decimal("12.50"))]
    already_canonical = [("2026-01-01", "12.50")]
    assert _row_checksum(native) == _row_checksum(already_canonical)


def test_row_checksum_of_an_empty_result_is_stable() -> None:
    assert _row_checksum([]) == _row_checksum([])


# -----------------------------------------------------------------------------
# _read_mart
# -----------------------------------------------------------------------------


def test_read_mart_raises_when_the_mart_has_not_been_built(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="run the gold build first"):
        _read_mart(tmp_path, "dispute_cases_monthly")


# -----------------------------------------------------------------------------
# _verify_output_digests
# -----------------------------------------------------------------------------


def _write_tiny_gold_manifest(gold_dir: Path, marts: dict[str, str]) -> None:
    """A gold directory with one tiny Parquet-shaped file per named mart and a manifest whose
    digests genuinely match, so a test states only which mart it means to break."""
    gold_dir.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict[str, object]] = {}
    for name, content in marts.items():
        path = gold_dir / f"{name}.parquet"
        path.write_bytes(content.encode())
        entries[name] = {"rows": 1, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (gold_dir / MANIFEST_NAME).write_text(
        json.dumps({"code_version": "test", "inputs": {}, "marts": entries}), encoding="utf-8"
    )


def test_verify_output_digests_raises_when_the_manifest_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="run the gold build first"):
        _verify_output_digests(tmp_path)


def test_verify_output_digests_raises_when_the_manifest_carries_no_marts_entry(
    tmp_path: Path,
) -> None:
    (tmp_path / MANIFEST_NAME).write_text(json.dumps({"code_version": "test"}), encoding="utf-8")
    with pytest.raises(ValueError, match="carries no marts entry"):
        _verify_output_digests(tmp_path)


def test_verify_output_digests_raises_when_a_mart_file_is_missing(tmp_path: Path) -> None:
    marts = {name: f"content of {name}" for name in MART_NAMES}
    _write_tiny_gold_manifest(tmp_path, marts)
    (tmp_path / f"{MART_NAMES[0]}.parquet").unlink()
    with pytest.raises(FileNotFoundError, match="run the gold build first"):
        _verify_output_digests(tmp_path)


def test_verify_output_digests_raises_when_one_mart_has_no_recorded_digest(tmp_path: Path) -> None:
    marts = {name: f"content of {name}" for name in MART_NAMES}
    _write_tiny_gold_manifest(tmp_path, marts)
    manifest = json.loads((tmp_path / MANIFEST_NAME).read_text(encoding="utf-8"))
    del manifest["marts"][MART_NAMES[0]]["sha256"]
    (tmp_path / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="carries no digest for"):
        _verify_output_digests(tmp_path)


def test_verify_output_digests_raises_when_a_mart_is_tampered(tmp_path: Path) -> None:
    marts = {name: f"content of {name}" for name in MART_NAMES}
    _write_tiny_gold_manifest(tmp_path, marts)
    (tmp_path / f"{MART_NAMES[0]}.parquet").write_bytes(b"tampered content")
    with pytest.raises(ValueError, match="does not match its manifest digest"):
        _verify_output_digests(tmp_path)


def test_verify_output_digests_passes_when_every_mart_matches_its_manifest(tmp_path: Path) -> None:
    marts = {name: f"content of {name}" for name in MART_NAMES}
    _write_tiny_gold_manifest(tmp_path, marts)
    _verify_output_digests(tmp_path)  # does not raise


def test_load_marts_refuses_a_tampered_mart_before_ever_connecting(tmp_path: Path) -> None:
    """The digest check runs before any database connection: an unreachable DSN still surfaces
    the ValueError, not a connection error, proving the check happens first."""
    marts = {name: f"content of {name}" for name in MART_NAMES}
    _write_tiny_gold_manifest(tmp_path, marts)
    (tmp_path / f"{MART_NAMES[0]}.parquet").write_bytes(b"tampered content")
    with pytest.raises(ValueError, match="does not match its manifest digest"):
        load_marts("postgresql://unreachable.invalid/nowhere", tmp_path)


# -----------------------------------------------------------------------------
# load_marts (integration)
# -----------------------------------------------------------------------------


@pytest.fixture
def gold(tmp_path: Path) -> Path:
    """The real dispute-demand marts, built from the shared service-dataset fixture."""
    raw = tmp_path / "raw"
    build_service_dataset(raw)
    silver = tmp_path / "silver"
    run_silver(raw, silver, code_version="test")
    target = tmp_path / "gold"
    build_marts(silver, target, code_version="test")
    return target


@pytest.mark.integration
def test_load_marts_loads_every_mart_and_verifies_parity(gold: Path) -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)

    result = load_marts(dsn, gold)

    assert isinstance(result, LoadResult)
    assert set(result.parity) == set(MART_NAMES)
    assert isinstance(result.parity["dispute_cases_monthly"], MartParity)
    assert result.parity["dispute_cases_monthly"].rows == 2  # January and February

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM analytics.dispute_cases_monthly")
        assert cur.fetchone() == (2,)
        cur.execute("SELECT month, cases FROM analytics.dispute_cases_monthly ORDER BY month")
        assert cur.fetchall() == [(date(2025, 1, 1), 3), (date(2025, 2, 1), 3)]


@pytest.mark.integration
def test_load_marts_truncates_and_reloads_rather_than_duplicating(gold: Path) -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)

    first = load_marts(dsn, gold)
    second = load_marts(dsn, gold)

    assert first.parity == second.parity
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM analytics.dispute_cases_monthly")
        assert cur.fetchone() == (2,)


@pytest.mark.integration
def test_load_marts_raises_and_rolls_back_when_postgres_checksum_disagrees(
    gold: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A defensive check that should never fire under correct behavior: forced here by making
    the loaded-side digest disagree with the source-side one, to prove the transaction never
    commits when it does."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)
    # A known-good baseline this test controls itself, so the assertion below proves the failed
    # load changed nothing, rather than assuming some other test left the table empty.
    load_marts(dsn, gold)

    real_checksum = analytics_load_module._row_checksum
    calls = {"n": 0}

    def _flip_second_call(rows: object) -> str:
        calls["n"] += 1
        checksum = real_checksum(rows)  # type: ignore[arg-type]
        return checksum if calls["n"] % 2 else f"{checksum}-tampered"

    monkeypatch.setattr(analytics_load_module, "_row_checksum", _flip_second_call)

    with pytest.raises(ValueError, match="does not match the gold mart's"):
        load_marts(dsn, gold)

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT month, cases FROM analytics.dispute_cases_monthly ORDER BY month")
        assert cur.fetchall() == [(date(2025, 1, 1), 3), (date(2025, 2, 1), 3)]


@pytest.mark.integration
def test_load_marts_raises_when_the_marts_have_not_been_built(tmp_path: Path) -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)

    with pytest.raises(FileNotFoundError):
        load_marts(dsn, tmp_path / "no-such-marts")


@pytest.mark.integration
def test_analytics_reader_role_can_read_but_not_write_the_analytics_schema(gold: Path) -> None:
    """The dashboard's own role (read-only, limited to `analytics`) never gets a grant this
    test does not see: SELECT on every mart table, nothing on the operational tables."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    apply_migrations(dsn)
    load_marts(dsn, gold)

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'analytics_reader' ORDER BY table_name, privilege_type"
        )
        grants = cur.fetchall()

    assert grants == [(name, "SELECT") for name in sorted(MART_NAMES)]
