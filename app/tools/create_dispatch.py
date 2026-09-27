"""
Case Creation Dispatch
=======================

Overview
--------
The one place in this codebase allowed to call ``ToolPort.create_dispute_case`` (ADR-3,
``Permission.CONTROLLER_ONLY``, AC-E4-38). The dialogue controller is the only caller: it has
already evaluated policy, decided the request is eligible and requires confirmation, and
collected the customer's explicit confirmation, before it ever reaches here.

Scope
-----
In: routing one already-validated ``CreateDisputeCaseRequest`` to the port, and nothing else.
Out: evaluating policy, deciding whether a request is eligible, collecting confirmation (the
controller, stream 2), and the permission invariants the tool itself enforces
(``app.persistence.reads.PostgresToolPort.create_dispute_case``).

Design Principles
------------------
- **A single reachable path** (AC-E4-38): every other module in ``app/`` is checked by a
  structural test to never reference ``create_dispute_case`` as an attribute; a second caller
  (a route, the console, a script) reopens the placement decision rather than quietly working
  around it.
- **No decision-making.** This function does not evaluate, retry or interpret the result; it
  passes the request through and returns exactly what the port returns.

Runtime Contract
-----------------
``create_dispute_case(port, request) -> CreateDisputeCaseResult | ToolFailure``

Limitations
-----------
Deliberately excluded from ``app.tools.dispatcher``'s table, per that module's own Limitations.
"""

from __future__ import annotations

from contracts.service_v1.tools import CreateDisputeCaseRequest, CreateDisputeCaseResult, ToolPort
from contracts.service_v1.tools import ToolFailure as ToolFailureResult


def create_dispute_case(
    port: ToolPort, request: CreateDisputeCaseRequest
) -> CreateDisputeCaseResult | ToolFailureResult:
    """File ``request`` through ``port``; the sole sanctioned call site (AC-E4-38)."""
    return port.create_dispute_case(request)
