"""
Turns Route
===========

Overview
--------
The one route a customer's message reaches: ``POST /v1/turns``, exchanging a ``TurnRequest`` for
the ``TurnResponse`` the dialogue controller decides on. The controller is built fresh for this one
request, matching its own "built fresh per request" contract; nothing about one customer's turn is
shared with the next one through this route.

Scope
-----
In: the route itself and ``build_turns_router``, which takes a factory that builds the
controller's per-request collaborators from the authenticated principal.
Out: authenticating the request (``app.security.middleware.SessionAuthMiddleware``), the body-size
cap ahead of it, building the factory's own collaborators (the composition root, ``app.main``),
and everything the controller itself decides (``app.conversation.controller``).

Design Principles
-----------------
- The controller is never a singleton: ``controller_factory`` runs once per request, inside the
  handler, so a version conflict or a stale collaborator can never leak between two customers'
  turns.
- A ``ProblemError`` the controller raises (a version conflict, ...) is not caught here: the
  application's own registered exception handler renders it as the standard problem document.

Runtime Contract
----------------
``POST /v1/turns``  body ``TurnRequest``  -> 200 ``TurnResponse`` (session required).
``build_turns_router(*, controller_factory) -> APIRouter``.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Callable  # Type of the per-request controller factory

# Third-party libraries
from fastapi import APIRouter, Request  # Routing and request access

# Local modules
from app.api.auth import principal_of  # The authenticated principal, from the auth middleware
from app.conversation.controller import DialogueController
from app.security.sessions import Principal
from contracts.service_v1.api import TurnRequest, TurnResponse
from contracts.service_v1.tools import ToolPort

ControllerFactory = Callable[[Principal], DialogueController]
ToolPortDecorator = Callable[[Principal, ToolPort], ToolPort]


def build_turns_router(*, controller_factory: ControllerFactory) -> APIRouter:
    """Build the turns route.

    Parameters
    ----------
    controller_factory : ControllerFactory
        Builds a ``DialogueController`` scoped to one principal; called exactly once per request.
    """
    router = APIRouter()

    @router.post("/v1/turns")
    def post_turn(body: TurnRequest, request: Request) -> TurnResponse:
        """Process one customer turn and return its reply."""
        principal = principal_of(request)
        controller = controller_factory(principal)
        return controller.handle_turn(body, principal=principal)

    return router
