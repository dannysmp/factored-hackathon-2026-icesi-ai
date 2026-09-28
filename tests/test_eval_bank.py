"""
Evaluation Scenario Bank Tests
================================

Component: ``pipelines.eval_bank``. Hermetic: every row is a literal in the module itself, so a
build needs nothing but a temporary directory to write into.
"""

from __future__ import annotations

# Standard libraries
import ast
import json
import re
from pathlib import Path

# Third-party libraries
import pytest

# Local modules
from app.security.sessions import CUSTOMER_ID_PATTERN
from contracts.service_v1.cases import REF_PATTERN
from pipelines.eval_bank import (
    CUSTOMERS_NAME,
    MANIFEST_NAME,
    PRODUCTS_NAME,
    TRANSACTIONS_NAME,
    build_eval_bank,
    main,
    read_table,
)


def test_build_writes_the_three_tables_and_a_manifest(tmp_path: Path) -> None:
    manifest = build_eval_bank(tmp_path, code_version="test")

    assert (tmp_path / CUSTOMERS_NAME).is_file()
    assert (tmp_path / PRODUCTS_NAME).is_file()
    assert (tmp_path / TRANSACTIONS_NAME).is_file()
    assert (tmp_path / MANIFEST_NAME).is_file()
    assert manifest.code_version == "test"
    assert manifest.rows == {
        "customers.parquet": 1,
        "products.parquet": 1,
        "transactions.parquet": 4,
    }
    assert set(manifest.output_sha256) == {CUSTOMERS_NAME, PRODUCTS_NAME, TRANSACTIONS_NAME}


def test_the_command_writes_the_bank_and_is_idempotent(tmp_path: Path) -> None:
    exit_code = main(["--gold", str(tmp_path / "gold"), "--code-version", "test"])
    assert exit_code == 0
    first = (tmp_path / "gold" / MANIFEST_NAME).read_bytes()

    exit_code = main(["--gold", str(tmp_path / "gold"), "--code-version", "test"])
    assert exit_code == 0
    assert (tmp_path / "gold" / MANIFEST_NAME).read_bytes() == first


def test_the_command_derives_a_code_version_when_none_is_given(tmp_path: Path) -> None:
    exit_code = main(["--gold", str(tmp_path / "gold")])
    assert exit_code == 0
    manifest = json.loads((tmp_path / "gold" / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["code_version"]


def test_build_is_deterministic(tmp_path: Path) -> None:
    """Byte-identical output on a second run: nothing here reads the clock or the environment."""
    first = build_eval_bank(tmp_path / "a", code_version="test")
    second = build_eval_bank(tmp_path / "b", code_version="test")

    assert first.output_sha256 == second.output_sha256


def test_the_manifest_names_every_scenarios_condition(tmp_path: Path) -> None:
    manifest = build_eval_bank(tmp_path, code_version="test")

    assert manifest.scenarios == {
        "TRX-EVALBANK-ORPHAN": "orphan_transaction",
        "TRX-EVALBANK-NO-MERCHANT": "missing_merchant_name",
        "TRX-EVALBANK-POISONED-MERCHANT": "injected_merchant_name",
        "TRX-EVALBANK-UNKNOWN-AMOUNT": "unknown_amount",
    }


def test_the_manifest_on_disk_matches_the_returned_object(tmp_path: Path) -> None:
    manifest = build_eval_bank(tmp_path, code_version="test")
    on_disk = json.loads((tmp_path / MANIFEST_NAME).read_text(encoding="utf-8"))

    assert on_disk == manifest.as_dict()


# -----------------------------------------------------------------------------
# read_table
# -----------------------------------------------------------------------------


def test_read_table_raises_when_the_bank_has_not_been_built(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="run the eval-bank build first"):
        read_table(tmp_path, TRANSACTIONS_NAME)


def test_the_orphan_transaction_references_no_known_customer_or_product(tmp_path: Path) -> None:
    build_eval_bank(tmp_path, code_version="test")
    customers = {row["customer_id"] for row in read_table(tmp_path, CUSTOMERS_NAME)}
    products = {row["product_id"] for row in read_table(tmp_path, PRODUCTS_NAME)}
    transactions = {row["transaction_id"]: row for row in read_table(tmp_path, TRANSACTIONS_NAME)}

    orphan = transactions["TRX-EVALBANK-ORPHAN"]
    assert orphan["customer_id"] not in customers
    assert orphan["product_id"] not in products


def test_the_no_merchant_scenario_carries_a_null_merchant_name(tmp_path: Path) -> None:
    build_eval_bank(tmp_path, code_version="test")
    transactions = {row["transaction_id"]: row for row in read_table(tmp_path, TRANSACTIONS_NAME)}

    assert transactions["TRX-EVALBANK-NO-MERCHANT"]["merchant_name"] is None


def test_the_poisoned_merchant_scenario_carries_an_injection_payload(tmp_path: Path) -> None:
    build_eval_bank(tmp_path, code_version="test")
    transactions = {row["transaction_id"]: row for row in read_table(tmp_path, TRANSACTIONS_NAME)}

    merchant = transactions["TRX-EVALBANK-POISONED-MERCHANT"]["merchant_name"]
    assert merchant is not None
    assert "ignore" in merchant.lower()


def test_the_unknown_amount_scenario_carries_no_disclosed_figure(tmp_path: Path) -> None:
    build_eval_bank(tmp_path, code_version="test")
    transactions = {row["transaction_id"]: row for row in read_table(tmp_path, TRANSACTIONS_NAME)}

    unknown = transactions["TRX-EVALBANK-UNKNOWN-AMOUNT"]
    assert unknown["amount_usd"] is None
    assert unknown["amount_usd_provenance"] == "unknown"


def test_every_transaction_date_is_a_fixed_literal_not_relative_to_today(tmp_path: Path) -> None:
    """Pins the no-clock design principle: every date is a plain literal in 2026-06."""
    build_eval_bank(tmp_path, code_version="test")
    for row in read_table(tmp_path, TRANSACTIONS_NAME):
        assert row["transaction_date"].date().year == 2026
        assert row["transaction_date"].date().month == 6


def test_every_transaction_id_matches_the_shared_reference_pattern(tmp_path: Path) -> None:
    """Any reader that resolves a `Case.seed_ref` must accept these ids the same way it accepts
    a seed id: both are checked against `contracts.service_v1.cases.REF_PATTERN`."""
    build_eval_bank(tmp_path, code_version="test")
    for row in read_table(tmp_path, TRANSACTIONS_NAME):
        assert re.fullmatch(REF_PATTERN, row["transaction_id"])


def test_every_customer_and_product_id_matches_the_sessions_stricter_pattern(
    tmp_path: Path,
) -> None:
    """`customer_id`/`product_id` are also checked against the session layer's own, shorter
    pattern (`app.security.sessions.CUSTOMER_ID_PATTERN`, at most 20 characters) — a looser bound
    here would look fine in this bank and fail the moment a real customer-facing path read it,
    including the orphan scenario's deliberately unresolvable identifiers."""
    build_eval_bank(tmp_path, code_version="test")
    for row in read_table(tmp_path, CUSTOMERS_NAME):
        assert CUSTOMER_ID_PATTERN.fullmatch(row["customer_id"])
    for row in read_table(tmp_path, PRODUCTS_NAME):
        assert CUSTOMER_ID_PATTERN.fullmatch(row["product_id"])
        assert CUSTOMER_ID_PATTERN.fullmatch(row["customer_id"])
    for row in read_table(tmp_path, TRANSACTIONS_NAME):
        assert CUSTOMER_ID_PATTERN.fullmatch(row["customer_id"])
        assert CUSTOMER_ID_PATTERN.fullmatch(row["product_id"])


def test_a_non_orphan_transactions_customer_and_product_both_exist(tmp_path: Path) -> None:
    build_eval_bank(tmp_path, code_version="test")
    customers = {row["customer_id"] for row in read_table(tmp_path, CUSTOMERS_NAME)}
    products = {row["product_id"] for row in read_table(tmp_path, PRODUCTS_NAME)}

    for row in read_table(tmp_path, TRANSACTIONS_NAME):
        if row["transaction_id"] == "TRX-EVALBANK-ORPHAN":
            continue
        assert row["customer_id"] in customers
        assert row["product_id"] in products


# -----------------------------------------------------------------------------
# Purity
# -----------------------------------------------------------------------------

# The same checks `tests/test_policy_engine.py` runs over `app.domain.policy`'s pure modules,
# applied here since this module's own docstring makes the identical "no clock" claim.
_FORBIDDEN_ATTRIBUTES = {"today", "now", "utcnow", "getenv", "environ", "urandom"}
_FORBIDDEN_NAMES = {"input", "exec", "eval", "getenv", "uuid4", "random"}


def test_the_module_reads_neither_the_clock_nor_the_environment() -> None:
    """No reference to a clock or an environment variable: every date here is a plain literal."""
    tree = ast.parse(Path("pipelines/eval_bank.py").read_text(encoding="utf-8"))

    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

    assert attributes & _FORBIDDEN_ATTRIBUTES == set()
    assert names & _FORBIDDEN_NAMES == set()
