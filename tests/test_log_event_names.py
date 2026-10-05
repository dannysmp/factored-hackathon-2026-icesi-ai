"""
Log Event Name Fitness Test
=============================

Component: the codebase under ``app/``. Hermetic: an AST scan of source text, no interpreter
spawned and no import executed.

Protects the stable event names the logs promise: ``app.observability.logging``'s JSON
formatter takes the first whitespace-delimited token of a log message as its stable ``event``
field. This test is what turns that from an observed convention into a constructed guarantee — a
future call site that starts its message with a sentence instead of a snake_case event name fails
here, not silently in a log line nobody reads.

A handful of call sites (``app/persistence/reads.py``, ``dialogue_store.py``,
``handoff_outbox.py``) go through a shared ``_log_failure(event, ...)`` helper whose own log
message is literally ``"%s ..."`` — the real event name is the caller's own literal argument to
``_log_failure``, not the format string. Those are checked by scanning ``_log_failure`` call sites
instead of the ``logger.*`` call inside the helper.
"""

from __future__ import annotations

# Standard libraries
import ast  # Parse source without executing it
import re  # The event-name shape
from pathlib import Path  # Walk the app package's source tree

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_ROOT = _REPO_ROOT / "app"
_LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception"}
_INDIRECT_EVENT_HELPERS = {"_log_failure"}
_EVENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _string_arg(node: ast.Call, index: int) -> str | None:
    """The literal string value of ``node``'s positional argument ``index``, or ``None``."""
    if len(node.args) <= index:
        return None
    argument = node.args[index]
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None


def _logger_call_first_tokens(path: Path) -> list[tuple[int, str]]:
    """Every event-name-bearing call's line number and its first token.

    Two shapes are checked: a direct ``logger.<level>("event ...")`` call, and a call to one of
    ``_INDIRECT_EVENT_HELPERS`` whose own first argument is a literal event name (a shared helper
    that logs on the caller's behalf; see the module docstring). A call whose relevant argument is
    not a plain string literal (an f-string, a variable) is skipped: it cannot be checked
    statically, and no call site in the codebase does this today.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        message: str | None = None
        if node.func.attr in _LOG_METHODS and isinstance(node.func.value, ast.Name):
            if node.func.value.id == "logger":
                message = _string_arg(node, 0)
        elif node.func.attr in _INDIRECT_EVENT_HELPERS:
            message = _string_arg(node, 0)
        if message is None:
            continue
        first_token = message.split(" ", 1)[0] if message else ""
        if first_token == "%s":
            # The event name is a caller-supplied literal to one of _INDIRECT_EVENT_HELPERS,
            # checked at that call site instead — this format string itself names nothing.
            continue
        found.append((node.lineno, first_token))
    return found


def test_every_log_messages_first_token_is_a_stable_snake_case_event_name() -> None:
    offenders = []
    for path in _APP_ROOT.rglob("*.py"):
        for lineno, token in _logger_call_first_tokens(path):
            if not _EVENT_NAME_PATTERN.fullmatch(token):
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {token!r}")

    assert offenders == [], f"log messages with a non-event-shaped first token: {offenders}"


def test_the_scan_itself_finds_call_sites() -> None:
    """A scan that silently matched nothing would make the test above vacuously true."""
    total = sum(len(_logger_call_first_tokens(path)) for path in _APP_ROOT.rglob("*.py"))

    assert total >= 15
