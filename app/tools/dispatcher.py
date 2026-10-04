"""
Tool Dispatcher
===============

Overview
--------
Calls the ``ToolPort`` method that matches a ``Tool`` value, given a payload already shaped for
it. A caller that already knows which tool a controller named, and has already validated the
request into the right type, does not need a hand-written branch per tool to invoke it.

Scope
-----
In: routing the five tools ``ToolPort`` implements today (everything but ``create_dispute_case``,
which has its own dispatch).
Out: choosing which tool to call, validating the payload into its request type (the dialogue
controller), and ``create_dispute_case``'s own dispatch.

Design Principles
------------------
- A closed mapping: every tool `dispatch` knows about is listed once, so an unhandled tool is a
  loud ``KeyError`` at the call site, never a silent no-op.
- No decision-making. This module routes; it does not decide what a result means, does not
  retry, and does not audit — every ``ToolPort`` implementation already does its own auditing.

Runtime Contract
-----------------
``dispatch(port, tool, payload) -> object``

Limitations
-----------
``create_dispute_case`` is deliberately not dispatched here: it carries permission invariants
(``Permission.CONTROLLER_ONLY``), so it has its own dispatch path, ``app.tools.create_dispatch``,
rather than sharing this table.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Callable  # Type of each dispatch-table entry
from typing import Any  # The payload's shape varies by tool

# Local modules
from contracts.service_v1.tools import Tool, ToolPort

_DISPATCH_TABLE: dict[Tool, Callable[[ToolPort, Any], Any]] = {
    Tool.LIST_TRANSACTIONS: lambda port, payload: port.list_transactions(payload),
    Tool.GET_TRANSACTION: lambda port, payload: port.get_transaction(payload),
    Tool.LIST_DISPUTE_CASES: lambda port, _payload: port.list_dispute_cases(),
    Tool.GET_CASE: lambda port, payload: port.get_case(payload),
    Tool.EVALUATE_DISPUTE: lambda port, payload: port.evaluate_dispute(payload),
}


def dispatch(port: ToolPort, tool: Tool, payload: Any = None) -> Any:
    """Call ``port``'s method for ``tool`` with ``payload``.

    Parameters
    ----------
    port : ToolPort
        Already scoped to one session's customer.
    tool : Tool
        Which method to call; ``payload``'s shape depends on it (a request model for
        ``list_transactions``/``evaluate_dispute``, a plain reference string for
        ``get_transaction``/``get_case``, ignored for ``list_dispute_cases``).

    Raises
    ------
    KeyError
        ``tool`` is ``create_dispute_case`` or otherwise not one this dispatcher routes.
    """
    try:
        call = _DISPATCH_TABLE[tool]
    except KeyError:
        raise KeyError(f"{tool} is not dispatched here") from None
    return call(port, payload)
