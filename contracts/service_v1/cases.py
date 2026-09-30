"""
Case Contract, Service Version 1
=================================

Overview
--------
What a dispute case is once filed: the record the case service holds, read back to verify a
filing and read again by every later lookup. This module is also the base the other stream-1
contracts (``tools``, ``audit``) build on: the shared primitives (the base model, the reference
patterns, an amount that states its own provenance, an instant that must be UTC) are defined once,
here.

Scope
-----
In: the case record, its closed set of statuses, and the primitives shared across this stream's
contracts.
Out: creating a case (``tools.create_dispute_case``), the mock case-service implementation and
its lifecycle advancement, and the policy decision a case rests on (``app.domain.policy``, reused
here, not redefined).

Design Principles
-----------------
- Two clocks, both on every record: the domain date the filing decision used (also the date shown
  to the customer as the day the case was filed) and the real UTC instant the row was written.
  Neither substitutes for the other (ADR-15).
- An amount never appears without saying where it came from: the source's own figure, a same-day
  conversion, or neither, in which case there is no figure to show, only the fact that there is
  none.
- The policy version and the reason code travel with every case row, so an independent read can
  ask whether the stored decision was really eligible without re-deriving it from the transcript.
- Closed sets are enumerations: a status or a provenance is added, never renamed; any other change
  needs a new contract package (``service_v2``) and a change request.
- The models are immutable and reject unknown fields, so a misspelled key fails at the boundary,
  not silently.

Runtime Contract
-----------------
``CaseRecord`` with ``CaseStatus``; the shared primitives ``ContractModel``, ``Lang``,
``REF_PATTERN``, ``NUMBER_PATTERN``, ``UtcDatetime``, ``SafeText``, ``Money``, ``AmountProvenance``
and ``DisclosedAmount``, imported by ``tools.py`` and ``audit.py``.

Limitations
-----------
The record carries no free text of the customer's own words: only the category and the structured
details the conversation collected. Append-only is a property of the store, not of this type; a
Postgres constraint enforces it starting with the serving-store slice.
"""

from __future__ import annotations

# Standard libraries
import unicodedata  # Control characters in system-held text
from datetime import date, datetime  # Domain dates and the real filing instant
from decimal import Decimal  # Money is never a float
from enum import StrEnum  # Closed sets of the contract
from typing import Annotated, Literal  # Bounded fields and the closed set of languages

# Third-party libraries
from pydantic import (  # Validated immutable models
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

# Local modules
from app.domain.policy.models import DisputeCategory, ReasonCode  # One vocabulary, not redefined

# -----------------------------------------------------------------------------
# Shared primitives (imported by tools.py and audit.py)
# -----------------------------------------------------------------------------

Lang = Literal["es", "pt", "en"]

# Identifier shapes: opaque references of at most 64 characters, and shorter case numbers,
# idempotency keys and trace identifiers; no spaces, so an identifier cannot carry a sentence.
REF_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
NUMBER_PATTERN = r"^[A-Za-z0-9_-]{1,32}$"


def _refuse_control_characters(value: str) -> str:
    """Refuse a control character or a formatting character, which no system-held field needs.

    Category ``Cc`` is the plain control characters; ``Cf`` adds the invisible formatting
    characters, including the bidirectional overrides a right-to-left character can use to make
    text render in an order that misleads a reader about what it says.
    """
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in value):
        raise ValueError("text must not contain a control or formatting character")
    return value


def _require_utc(value: datetime) -> datetime:
    """Refuse an instant that is not expressed in UTC."""
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("instants of record must be in UTC")
    return value


# System-held free text: sourced from the cleaned data, never typed by a customer in the
# conversation, but still refused a control character before it reaches a render or a log.
SafeText = Annotated[str, AfterValidator(_refuse_control_characters)]

# An instant of record, always UTC: session issue and expiry, audit occurrence, case filing.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]


class ContractModel(BaseModel):
    """Base of every contract model in this stream: immutable, and unknown fields are an error."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Money(ContractModel):
    """An amount with its currency code; an amount never appears without one."""

    amount: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class AmountProvenance(StrEnum):
    """Where a disclosed amount came from."""

    REPORTED = "reported"
    CONVERTED = "converted"
    UNKNOWN = "unknown"


class DisclosedAmount(ContractModel):
    """A transaction amount as the source states it, converted, or absent — never invented.

    It is the amount the source states, marked ``reported``; when none is stated and a rate
    exists for the day, it is the amount converted at that rate, marked ``converted``; it is
    marked ``unknown`` only when neither is available, and then carries no figure.
    """

    money: Money | None
    provenance: AmountProvenance

    @model_validator(mode="after")
    def _money_matches_provenance(self) -> DisclosedAmount:
        """A figure is present exactly when the provenance is not unknown."""
        if (self.provenance is AmountProvenance.UNKNOWN) == (self.money is not None):
            raise ValueError("money is present exactly when the provenance is not unknown")
        return self


# -----------------------------------------------------------------------------
# Case record
# -----------------------------------------------------------------------------


class CaseStatus(StrEnum):
    """Status of a filed case.

    The seed advances a case from Open through In Review to Resolved. Rejected, and any change to
    a case once it reaches Resolved or Rejected, is reachable only by a narrow, audited agent
    write (``contracts.service_v1.console.SetCaseStatusRequest``) — Open and In Review are the
    only statuses a further status-set may still change, never the seed or a customer tool.
    """

    OPEN = "Open"
    IN_REVIEW = "In Review"
    RESOLVED = "Resolved"
    REJECTED = "Rejected"


class CaseRecord(ContractModel):
    """One dispute case as filed: read back to verify, and read again by every later lookup.

    Customer-visible dates are domain dates, the data reference date, not the real date; the
    console shows both, labeled. No free text of the customer is stored: only the category and
    the structured details the conversation collected.
    """

    case_number: Annotated[str, Field(pattern=NUMBER_PATTERN)]
    status: CaseStatus
    transaction_ref: Annotated[str, Field(pattern=REF_PATTERN)]
    category: DisputeCategory
    amount: DisclosedAmount
    domain_date: date
    expected_first_response_date: date
    created_at_utc: UtcDatetime
    policy_version: Annotated[str, Field(min_length=1)]
    reason_code: ReasonCode
    language: Lang

    @model_validator(mode="after")
    def _response_is_not_before_filing(self) -> CaseRecord:
        """The first response is expected on or after the domain date the case was filed on."""
        if self.expected_first_response_date < self.domain_date:
            raise ValueError("expected_first_response_date is before domain_date")
        return self
