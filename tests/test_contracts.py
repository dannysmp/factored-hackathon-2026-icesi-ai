"""
Contract Tests
==============

Component: ``contracts.v1``. Hermetic and pure: the contract is declarative data, so the tests
check that it agrees with the table registry, that its reference actions follow the stated rule
and that the dependency order is valid.
"""

from __future__ import annotations

# Third-party libraries
import pytest  # Test runner and fixtures

# Local modules
from contracts import v1
from contracts.v1 import (
    CONTRACT_VERSION,
    ReferenceAction,
    contract_for,
    load_order,
)
from pipelines.sources import TABLES, table


@pytest.mark.parametrize("spec", TABLES, ids=lambda spec: spec.name)
def test_every_rule_names_a_declared_column(spec: object) -> None:
    """Allowed values, ranges and canonical spellings only mention columns the table declares."""
    name = getattr(spec, "name")  # noqa: B009 - parametrized object typed as object
    columns = set(table(name).column_names)
    contract = contract_for(name)

    assert {rule.column for rule in contract.allowed} <= columns
    assert {rule.column for rule in contract.ranges} <= columns
    assert {rule.column for rule in contract.canonical} <= columns
    assert contract.version == CONTRACT_VERSION


def test_canonical_spellings_map_into_the_allowed_values() -> None:
    """A rewritten value must be one the contract then accepts."""
    transactions = contract_for("transactions")
    allowed = {rule.column: rule.values for rule in transactions.allowed}

    for rule in transactions.canonical:
        for _old, new in rule.mapping:
            assert new in allowed[rule.column]
        for old, _new in rule.mapping:
            assert old not in allowed[rule.column]


def test_ranges_are_ordered() -> None:
    """A range never has its minimum above its maximum."""
    for spec in TABLES:
        for rule in contract_for(spec.name).ranges:
            assert rule.minimum < rule.maximum, (spec.name, rule.column)


def test_reference_actions_follow_the_stated_rule() -> None:
    """Required references quarantine, except to branches; optional ones and branches only flag."""
    actions = {
        (spec.name, rule.column): rule.action
        for spec in TABLES
        for rule in contract_for(spec.name).references
    }

    assert actions[("transactions", "customer_id")] is ReferenceAction.QUARANTINE
    assert actions[("transactions", "product_id")] is ReferenceAction.QUARANTINE
    assert actions[("complaints", "customer_id")] is ReferenceAction.QUARANTINE
    assert actions[("transactions", "branch_id")] is ReferenceAction.FLAG  # optional and a branch
    assert (
        actions[("customers", "registration_branch_id")] is ReferenceAction.FLAG
    )  # required, branch
    assert actions[("complaints", "origin_interaction_id")] is ReferenceAction.FLAG  # optional


def test_every_foreign_key_of_the_registry_has_exactly_one_rule() -> None:
    """The contract covers each declared foreign key once."""
    for spec in TABLES:
        declared = sorted(fk.column for fk in spec.foreign_keys)
        ruled = sorted(rule.column for rule in contract_for(spec.name).references)
        assert ruled == declared, spec.name


def test_load_order_puts_every_table_after_the_tables_it_references() -> None:
    """Referenced tables are cleaned first, so references can be checked against their output."""
    order = load_order()

    assert sorted(order) == sorted(spec.name for spec in TABLES)
    for spec in TABLES:
        for fk in spec.foreign_keys:
            assert order.index(fk.ref_table) < order.index(spec.name), (spec.name, fk.ref_table)


def test_cyclic_references_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two tables that reference each other have no valid order, and the error names them."""
    from dataclasses import replace  # noqa: PLC0415 - local to this test

    from pipelines.sources import ForeignKey  # noqa: PLC0415

    a, b = TABLES[0], TABLES[1]
    cyclic = (
        replace(a, foreign_keys=(ForeignKey("x", b.name, "y"),)),
        replace(b, foreign_keys=(ForeignKey("y", a.name, "x"),)),
    )
    monkeypatch.setattr(v1, "TABLES", cyclic)

    with pytest.raises(ValueError, match=f"{a.name}"):
        load_order()


def test_unknown_table_has_no_contract() -> None:
    """Asking for a table outside the registry fails with its name."""
    with pytest.raises(KeyError, match="orders"):
        contract_for("orders")
