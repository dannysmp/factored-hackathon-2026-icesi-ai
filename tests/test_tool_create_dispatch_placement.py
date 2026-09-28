"""
Case Creation Placement Test
==============================

Component: the codebase under ``app/``. Hermetic: an AST scan of source text, no interpreter
spawned and no import executed.

Protects AC-E4-38: the create tool (``ToolPort.create_dispute_case``) is reachable from exactly
two sanctioned files, never a third. ``app.tools.create_dispatch`` is the one place the dialogue
controller calls into; ``app.reliability.tool_port`` is a generic ``ToolPort`` decorator (E9)
that delegates every method of the protocol, including this one, to whichever port it wraps —
it is reached only through the dispatcher's own sanctioned call (``port.create_dispute_case``,
where ``port`` is the decorator instance) and never an independent entry point of its own, so it
sits transparently inside the one sanctioned path rather than opening a second one. A caller
outside both files — a route, the console, a script, or a future controller reaching past the
sanctioned entry point — reopens the placement decision rather than quietly working around it.
The scan is by attribute name, not by import, so it also catches a caller that reaches the method
through a differently named alias of the port.
"""

from __future__ import annotations

# Standard libraries
import ast  # Parse source without executing it
from pathlib import Path  # Walk the app package's source tree

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_ROOT = _REPO_ROOT / "app"
_METHOD_NAME = "create_dispute_case"
_SANCTIONED_FILES = frozenset(
    {
        _APP_ROOT / "tools" / "create_dispatch.py",
        _APP_ROOT / "reliability" / "tool_port.py",
    }
)


def _references_the_method(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Attribute) and node.attr == _METHOD_NAME for node in ast.walk(tree)
    )


def test_create_dispute_case_is_reachable_only_from_sanctioned_files() -> None:
    """No file under ``app/`` other than the two sanctioned ones references the method by name."""
    offenders = [
        path
        for path in _APP_ROOT.rglob("*.py")
        if path not in _SANCTIONED_FILES and _references_the_method(path)
    ]

    assert offenders == [], (
        f"create_dispute_case is referenced outside the sanctioned files: {offenders}"
    )


def test_every_sanctioned_file_actually_calls_the_method() -> None:
    """Neither designated file is dead code: both do reference the method."""
    assert all(_references_the_method(path) for path in _SANCTIONED_FILES)
