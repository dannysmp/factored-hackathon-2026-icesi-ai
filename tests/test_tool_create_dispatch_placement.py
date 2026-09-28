"""
Case Creation Placement Test
==============================

Component: the codebase under ``app/``. Hermetic: an AST scan of source text, no interpreter
spawned and no import executed.

Protects AC-E4-38: the create tool (``ToolPort.create_dispute_case``) is reachable from exactly
one place, ``app.tools.create_dispatch``. A second caller — a route, the console, a script, or a
future controller reaching past the sanctioned entry point — reopens the placement decision
rather than quietly working around it. The scan is by attribute name, not by import, so it also
catches a caller that reaches the method through a differently named alias of the port.
"""

from __future__ import annotations

# Standard libraries
import ast  # Parse source without executing it
from pathlib import Path  # Walk the app package's source tree

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_ROOT = _REPO_ROOT / "app"
_METHOD_NAME = "create_dispute_case"
_SANCTIONED_CALLER = _APP_ROOT / "tools" / "create_dispatch.py"


def _references_the_method(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Attribute) and node.attr == _METHOD_NAME for node in ast.walk(tree)
    )


def test_create_dispute_case_is_reachable_only_from_the_sanctioned_dispatcher() -> None:
    """No file under ``app/`` other than the sanctioned dispatcher references the method by name."""
    offenders = [
        path
        for path in _APP_ROOT.rglob("*.py")
        if path != _SANCTIONED_CALLER and _references_the_method(path)
    ]

    assert offenders == [], (
        f"create_dispute_case is referenced outside app/tools/create_dispatch.py: {offenders}"
    )


def test_the_sanctioned_dispatcher_itself_calls_the_method() -> None:
    """The designated caller is not itself dead code: it does reference the method."""
    assert _references_the_method(_SANCTIONED_CALLER)
