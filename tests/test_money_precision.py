"""
Money Precision Tests
=====================

Component: the digit bound of the contracts' money fields against the serving store's amount
columns. Hermetic: the store's declared precision is read from the migration text, so the test
fails the moment the two disagree in the direction that matters, a value the contract accepts and
the store would reject.
"""

from __future__ import annotations

# Standard libraries
import re  # Reading a column's declared precision out of the migration
from decimal import Decimal  # Money is never a float
from pathlib import Path  # The migration's location

# Third-party libraries
import pytest  # Parametrization and expected refusals
from pydantic import ValidationError  # The refusal of an over-wide amount

# Local modules
from contracts.service_v1 import cases, envelope, nlu

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "persistence"
    / "migrations"
    / "0001_serving_store.sql"
)
LARGEST_ACCEPTED = Decimal("999999999999.99")
FIRST_REFUSED = Decimal("1000000000000.00")


def _column_precision(column: str, table: str) -> tuple[int, int]:
    """The declared (precision, scale) of one NUMERIC column of one table."""
    text = MIGRATION.read_text(encoding="utf-8")
    block = re.search(rf"CREATE TABLE {table} \((.*?)\n\);", text, re.DOTALL)
    assert block is not None, table
    declared = re.search(rf"^\s+{column} NUMERIC\((\d+), (\d+)\)", block.group(1), re.MULTILINE)
    assert declared is not None, f"{table}.{column}"
    return int(declared.group(1)), int(declared.group(2))


def _build(model: str, amount: Decimal) -> object:
    if model == "cases":
        return cases.Money(amount=amount, currency="USD")
    if model == "envelope":
        return envelope.Money(amount=amount, currency="USD")
    return nlu.TransactionHint(amount=amount)


@pytest.mark.parametrize("model", ["cases", "envelope", "hint"])
def test_the_widest_amount_the_contract_accepts_is_twelve_integer_digits(model: str) -> None:
    assert _build(model, LARGEST_ACCEPTED) is not None
    with pytest.raises(ValidationError):
        _build(model, FIRST_REFUSED)


@pytest.mark.parametrize(
    ("column", "table"),
    [("amount", "transactions"), ("amount_usd", "transactions"), ("amount", "cases")],
)
def test_every_amount_the_contract_accepts_fits_the_store_column(column: str, table: str) -> None:
    precision, scale = _column_precision(column, table)
    integer_digits = precision - scale
    assert len(str(LARGEST_ACCEPTED).split(".")[0]) <= integer_digits
    assert scale == 2
