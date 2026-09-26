"""
Source Data Contract, Version 1
===============================

Overview
--------
The first released contract for the 13 source tables. Structure (columns, types, nullability,
primary keys) comes from the table registry; this module adds the rules the registry cannot
express: allowed values of coded columns, numeric ranges, canonical spellings and the action
taken when a reference points at no existing row.

Scope
-----
In: declarative rules and ``contract_for``.
Out: applying them (``pipelines.silver``).

Design Principles
-----------------
- Allowed values are the ones observed in the data (see the data profile), not the ones the
  data dictionary promises: the dictionary lists English labels where the data holds Spanish
  ones. A value outside the list sends the row to quarantine, so a new label is noticed.
- The source's branch references cannot be resolved (their orphan rate is close to 100 %), so
  rows are flagged, not dropped, for them: dropping would empty the customer, product and agent
  dimensions. Every other required reference quarantines the row.
- The contract is immutable once released; a change creates ``v2``.

Runtime Contract
----------------
``CONTRACT_VERSION``: the version string recorded in every manifest.
``contract_for(table_name) -> TableContract``.

Limitations
-----------
Value lists reflect a single snapshot of the data; unseen but legitimate labels must be added
in a new version.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass  # Immutable rule objects
from enum import StrEnum  # Closed set of reference actions

# Local modules
from pipelines.sources import TABLES, TableSpec, table  # Structural facts of each table

# -----------------------------------------------------------------------------
# Types
# -----------------------------------------------------------------------------

CONTRACT_VERSION = "1"


class ReferenceAction(StrEnum):
    """What happens to a row whose reference points at no existing row."""

    QUARANTINE = "quarantine"
    FLAG = "flag"


@dataclass(frozen=True, slots=True)
class AllowedValues:
    """A coded column may only hold one of ``values``."""

    column: str
    values: frozenset[str]


@dataclass(frozen=True, slots=True)
class ValueRange:
    """A numeric column must lie within ``minimum`` and ``maximum`` inclusive."""

    column: str
    minimum: float
    maximum: float


@dataclass(frozen=True, slots=True)
class Canonicalization:
    """Spellings of one value are rewritten to its canonical form before the checks run."""

    column: str
    mapping: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ReferenceRule:
    """The action for orphans of one foreign key."""

    column: str
    ref_table: str
    ref_column: str
    action: ReferenceAction


@dataclass(frozen=True, slots=True)
class TableContract:
    """Everything the cleaned layer requires of one table beyond its structure."""

    table: str
    version: str
    allowed: tuple[AllowedValues, ...]
    ranges: tuple[ValueRange, ...]
    canonical: tuple[Canonicalization, ...]
    references: tuple[ReferenceRule, ...]


# -----------------------------------------------------------------------------
# Rules
# -----------------------------------------------------------------------------

_CURRENCIES = frozenset({"MXN", "COP", "ARS", "USD"})
_COUNTRIES = frozenset({"México", "Colombia", "Argentina"})

_ALLOWED: dict[str, tuple[AllowedValues, ...]] = {
    "customers": (
        AllowedValues("document_type", frozenset({"DNI", "CE", "Pasaporte", "CC"})),
        AllowedValues("gender", frozenset({"F", "M", "O"})),
        AllowedValues("customer_status", frozenset({"Active", "Inactive", "Suspended", "Closed"})),
        AllowedValues("segment", frozenset({"Basic", "Plus", "Premium", "Student"})),
        AllowedValues("country", _COUNTRIES),
    ),
    "products": (
        AllowedValues("currency", _CURRENCIES),
        AllowedValues("product_status", frozenset({"Active", "Closed", "Blocked", "Suspended"})),
    ),
    "transactions": (
        AllowedValues(
            "transaction_type",
            frozenset({"Purchase", "Withdrawal", "Transfer", "Payment", "Deposit", "Adjustment"}),
        ),
        AllowedValues("currency", _CURRENCIES),
        AllowedValues("channel", frozenset({"POS", "ATM", "Web", "App", "Branch", "Transfer"})),
        AllowedValues(
            "transaction_status", frozenset({"Approved", "Declined", "Pending", "Reversed"})
        ),
        AllowedValues(
            "transaction_country",
            _COUNTRIES | frozenset({"USA", "Spain", "Brazil"}),
        ),
    ),
    "call_center_interactions": (
        AllowedValues(
            "interaction_type",
            frozenset({"Inbound Call", "Outbound Call", "Chat", "Email", "Video"}),
        ),
    ),
    "complaints": (
        AllowedValues("case_type", frozenset({"Complaint", "Claim", "Request", "Suggestion"})),
        AllowedValues("priority", frozenset({"Low", "Medium", "High", "Critical"})),
        AllowedValues(
            "status",
            frozenset({"Open", "In Process", "Escalated", "Resolved", "Closed", "Rejected"}),
        ),
    ),
    "daily_exchange_rates": (
        AllowedValues("source_currency", _CURRENCIES),
        AllowedValues("target_currency", _CURRENCIES),
    ),
}

_RANGES: dict[str, tuple[ValueRange, ...]] = {
    "customers": (ValueRange("credit_score", 300, 850),),
    "transactions": (ValueRange("fraud_score", 0, 100),),
    "satisfaction_surveys": (ValueRange("main_score", 0, 10),),
}

# "Mexico" and "México" name the same country; the accented form is canonical.
_CANONICAL: dict[str, tuple[Canonicalization, ...]] = {
    "transactions": (Canonicalization("transaction_country", (("Mexico", "México"),)),),
}

# The source's references to branches never resolve, so they are flagged rather than enforced.
_UNENFORCEABLE_REFERENCE_TABLES = frozenset({"branches"})


def _reference_rules(spec: TableSpec) -> tuple[ReferenceRule, ...]:
    """Derive the action for each foreign key from the table registry.

    A required reference to anything but a branch quarantines the row when it is an orphan;
    optional references and references to branches are only flagged.
    """
    required = {column.name for column in spec.columns if not column.nullable}
    rules = []
    for fk in spec.foreign_keys:
        enforce = fk.column in required and fk.ref_table not in _UNENFORCEABLE_REFERENCE_TABLES
        action = ReferenceAction.QUARANTINE if enforce else ReferenceAction.FLAG
        rules.append(ReferenceRule(fk.column, fk.ref_table, fk.ref_column, action))
    return tuple(rules)


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def contract_for(table_name: str) -> TableContract:
    """Return the version 1 contract of ``table_name``.

    Raises
    ------
    KeyError
        When no such table is registered.
    """
    spec = table(table_name)
    return TableContract(
        table=table_name,
        version=CONTRACT_VERSION,
        allowed=_ALLOWED.get(table_name, ()),
        ranges=_RANGES.get(table_name, ()),
        canonical=_CANONICAL.get(table_name, ()),
        references=_reference_rules(spec),
    )


def load_order() -> tuple[str, ...]:
    """Table names in dependency order: a table comes after every table it references.

    Raises
    ------
    ValueError
        When the references between tables form a cycle.
    """
    remaining = {
        spec.name: {fk.ref_table for fk in spec.foreign_keys} - {spec.name} for spec in TABLES
    }
    ordered: list[str] = []
    while remaining:
        ready = sorted(name for name, needs in remaining.items() if needs <= set(ordered))
        if not ready:
            raise ValueError("cyclic references between tables: " + ", ".join(sorted(remaining)))
        ordered.extend(ready)
        for name in ready:
            del remaining[name]
    return tuple(ordered)
