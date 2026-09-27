"""
Customer API Contract, Service Version 1
========================================

Overview
--------
The request and response of a conversation turn, and the readiness payload. A turn carries the
customer's text and a client-chosen identifier so a retry does not advance the conversation
twice; the response carries the reply, the persistent reference-date line and what the client
needs to show the next step.

Scope
-----
In: the turn request and response and the readiness payload.
Out: the endpoint, the body-size cap, idempotency handling and authentication.

Design Principles
-----------------
- Nothing in a request names a customer, a session or a date: identity comes from the
  authenticated session and the domain date from the service's calendar, so a client cannot
  express another customer or another day.
- The reply, the reference-date line and the demonstration notice are rendered by the service in
  the conversation language; the client shows them and decides nothing.
- The response never carries the envelope, a reason code, a numeric routing input or a risk
  score.

Runtime Contract
----------------
``TurnRequest`` -> ``TurnResponse`` and ``ReadinessPayload``.

Limitations
-----------
The maximum text length is a contract bound; the byte cap on the request body is enforced by the
endpoint.
"""

from __future__ import annotations

# Standard libraries
from datetime import date  # The reference date of the data
from enum import StrEnum  # Closed set of reference-date origins
from typing import Annotated  # Bounded fields

# Third-party libraries
from pydantic import Field  # Field bounds

# Local modules
from contracts.service_v1.envelope import CONTRACT_VERSION, ContractModel, Lang, Slot

MAX_TEXT_LENGTH = 2000


class TurnRequest(ContractModel):
    """One customer message."""

    turn_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")]
    text: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)]


class Choice(ContractModel):
    """One numbered option the customer can pick, with the text to show for it."""

    number: Annotated[int, Field(ge=1, le=5)]
    label: Annotated[str, Field(min_length=1, max_length=200)]


class TurnResponse(ContractModel):
    """The reply to one turn."""

    contract_version: Annotated[str, Field(pattern=r"^\d+$")] = CONTRACT_VERSION
    turn_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")]
    conversation_id: Annotated[str, Field(min_length=1, max_length=64)]
    state_version: Annotated[int, Field(ge=1)]
    lang: Lang
    reply: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)]
    reference_date_line: Annotated[str, Field(min_length=1, max_length=120)]
    demo_notice: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    choices: Annotated[tuple[Choice, ...], Field(max_length=5)] = ()
    next_expected: Slot | None = None
    end_session: bool = False
    handoff_ticket: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")] | None = None


class ReferenceDateOrigin(StrEnum):
    """Where the reference date of the data came from."""

    SETTING = "setting"
    SEED = "seed"
    SYSTEM = "system"


class ReadinessPayload(ContractModel):
    """What the readiness endpoint reports, including the reference date and its origin."""

    status: Annotated[str, Field(pattern=r"^ready$")]
    service_version: Annotated[str, Field(min_length=1, max_length=64)]
    environment: Annotated[str, Field(min_length=1, max_length=16)]
    reference_date: date
    reference_date_origin: ReferenceDateOrigin
