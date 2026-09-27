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
import unicodedata  # Control characters in a message
from datetime import date  # The reference date of the data
from enum import StrEnum  # Closed set of reference-date origins
from typing import Annotated, Literal  # Bounded fields and closed values

# Third-party libraries
from pydantic import AfterValidator, Field, model_validator  # Field bounds and cross-field rules

# Local modules
from contracts.service_v1.envelope import (  # Shared base and types
    CONTRACT_VERSION,
    NUMBER_PATTERN,
    ContractModel,
    Lang,
    SafeText,
    Slot,
)

MAX_TEXT_LENGTH = 2000


def _refuse_blank_or_control(value: str) -> str:
    """Refuse a message that is only whitespace or holds a control character such as NUL."""
    if not value.strip():
        raise ValueError("text must not be blank")
    if any(unicodedata.category(char) == "Cc" and char != "\n" for char in value):
        raise ValueError("text must not contain control characters")
    return value


class TurnRequest(ContractModel):
    """One customer message.

    The text may hold a line break but no other control character. It is not screened for card
    numbers here: it is transient and masked before it leaves the service, and a customer who
    types one is answered, not refused with a schema error.
    """

    turn_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")]
    text: Annotated[
        str,
        Field(min_length=1, max_length=MAX_TEXT_LENGTH),
        AfterValidator(_refuse_blank_or_control),
    ]


class Choice(ContractModel):
    """One numbered option the customer can pick, with the text to show for it."""

    number: Annotated[int, Field(ge=1, le=5)]
    label: Annotated[SafeText, Field(min_length=1, max_length=200)]


class TurnResponse(ContractModel):
    """The reply to one turn."""

    contract_version: Literal["1"] = CONTRACT_VERSION
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
    handoff_ticket: Annotated[str, Field(pattern=NUMBER_PATTERN)] | None = None

    @model_validator(mode="after")
    def _choices_are_numbered_from_one(self) -> TurnResponse:
        """Choices are numbered 1, 2, ... in order, so a number picks exactly one."""
        if [choice.number for choice in self.choices] != list(range(1, len(self.choices) + 1)):
            raise ValueError("choices must be numbered from 1 without gaps or repeats")
        return self


class ReferenceDateOrigin(StrEnum):
    """Where the reference date of the data came from."""

    SETTING = "setting"
    SEED = "seed"
    SYSTEM = "system"


class ReadinessPayload(ContractModel):
    """What the readiness endpoint reports.

    Besides the versions it reports the reference date and its origin, and the version and digest
    of the policy file the service loaded, so a deployment on an explicit past date or on another
    policy cannot be mistaken for the expected one.
    """

    status: Literal["ready"]
    service_version: Annotated[str, Field(min_length=1, max_length=64)]
    environment: Annotated[str, Field(min_length=1, max_length=16)]
    reference_date: date
    reference_date_origin: ReferenceDateOrigin
    policy_version: Annotated[str, Field(min_length=1, max_length=32)]
    policy_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
