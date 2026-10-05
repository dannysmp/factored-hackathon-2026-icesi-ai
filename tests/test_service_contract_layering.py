"""
Layering and Scope Fitness Tests
==================================

Component: ``contracts.service_v1.tools``, ``.cases`` and ``.audit`` — the tool-side contract
files, which stand apart from the conversation contracts. Hermetic: static analysis of the
source, no interpreter spawned and no import executed beyond what the test module itself already
imports for the model-level checks.

Protects two rules that no code review catches reliably by eye:
- **Layering.** A contract module is declarative data plus a port interface; it depends on
  nothing that reads configuration, touches a network or a database, or belongs to the
  conversation contract files. An import outside the allowed set is a dependency nobody has
  reviewed, and a released contract must not gain one quietly.
- **Scope.** No tool request or filter can express a customer other than the session's own:
  the check runs once, generically, over every model these modules define, so a new
  model added later without a matching test still cannot smuggle a ``customer_id`` field in.
"""

from __future__ import annotations

# Standard libraries
import ast  # Parse imports without executing the module
import sys  # The interpreter's own standard-library module names
from pathlib import Path  # Locate the contract source files
from types import ModuleType  # Type of the imported contract modules

# Third-party libraries
from pydantic import BaseModel  # Base of every contract model, for the scope scan

# Local modules
import contracts.service_v1.audit as audit_module
import contracts.service_v1.cases as cases_module
import contracts.service_v1.tools as tools_module

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = _REPO_ROOT / "contracts" / "service_v1"

# What each file may import, by the root of the dotted path (``a.b.c`` -> ``a``, ``a.b`` for a
# two-level allowance). Standard-library modules are allowed everywhere and checked separately.
_ALLOWED_THIRD_PARTY = frozenset({"pydantic"})
_ALLOWED_LOCAL = {
    "cases.py": frozenset({"app.domain.policy.models"}),
    "tools.py": frozenset({"app.domain.policy.models", "contracts.service_v1.cases"}),
    "audit.py": frozenset({"app.domain.policy.models", "contracts.service_v1.cases"}),
}


def _imported_roots(source: str) -> set[str]:
    """Every module named in an ``import`` or ``from ... import`` statement, dotted path intact."""
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            roots.add(node.module)
    return roots


def _is_stdlib(module: str) -> bool:
    """Whether the top-level package of ``module`` is part of the standard library."""
    top = module.split(".", 1)[0]
    return top in sys.stdlib_module_names or top == "__future__"


def _check_layering(filename: str) -> None:
    """Fail when ``filename`` imports anything outside the standard library and its allowed set.

    Parameters
    ----------
    filename : str
        A key of ``_ALLOWED_LOCAL``: the module file inside the contract package to inspect.

    Raises
    ------
    AssertionError
        The file imports a module that is neither standard library, an allowed third-party
        package nor one of its allowed local modules; the message names the import.
    """
    imported = _imported_roots((_PACKAGE / filename).read_text(encoding="utf-8"))
    allowed_local = _ALLOWED_LOCAL[filename]
    for module in imported:
        if _is_stdlib(module):
            continue
        if module in _ALLOWED_THIRD_PARTY:
            continue
        if module in allowed_local:
            continue
        raise AssertionError(
            f"{filename} imports {module!r}, which is outside its allowed dependencies "
            f"({sorted(_ALLOWED_THIRD_PARTY | allowed_local)} plus the standard library)"
        )


def test_cases_depends_on_nothing_outside_its_allowed_set() -> None:
    """The base module depends on the policy vocabulary and nothing else project-owned."""
    _check_layering("cases.py")


def test_tools_depends_on_nothing_outside_its_allowed_set() -> None:
    """The tool contract depends only on the policy vocabulary and its own package base."""
    _check_layering("tools.py")


def test_audit_depends_on_nothing_outside_its_allowed_set() -> None:
    """The audit contract depends only on the policy vocabulary and its own package base."""
    _check_layering("audit.py")


def test_no_tool_contract_file_imports_the_conversation_contracts() -> None:
    """None of the six conversation-contract files is named by an import in a tool-side file."""
    forbidden = {
        "contracts.service_v1.envelope",
        "contracts.service_v1.nlu",
        "contracts.service_v1.handoff",
        "contracts.service_v1.api",
        "contracts.service_v1.console",
        "contracts.service_v1.verification",
    }
    for filename in _ALLOWED_LOCAL:
        imported = _imported_roots((_PACKAGE / filename).read_text(encoding="utf-8"))
        assert imported.isdisjoint(forbidden), f"{filename} imports {imported & forbidden}"


def _contract_models(module: ModuleType) -> list[type[BaseModel]]:
    """Every ``BaseModel`` subclass this module defines itself (not one it only imports)."""
    return [
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value.__module__ == module.__name__
    ]


def test_no_contract_model_can_express_a_customer_identifier() -> None:
    """The session supplies the customer, never a request field, checked over every model.

    A model added to any of these three modules without its own dedicated test still cannot
    smuggle a ``customer_id`` in, because this scan covers every model the modules define.
    """
    offenders = [
        f"{model.__module__}.{model.__name__}"
        for module in (tools_module, cases_module, audit_module)
        for model in _contract_models(module)
        for field_name in model.model_fields
        if "customer_id" in field_name and model.__name__ != "AuditRecord"
    ]

    assert offenders == []


def test_the_audit_record_is_the_only_model_naming_a_customer_identifier() -> None:
    """The audit record identifies who acted; every other model reaches only the session's own."""
    assert "customer_id" in audit_module.AuditRecord.model_fields
    other_models = [
        model for module in (tools_module, cases_module) for model in _contract_models(module)
    ]
    assert all("customer_id" not in model.model_fields for model in other_models)
