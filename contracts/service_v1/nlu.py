"""
Language Understanding Contract, Service Version 1
==================================================

Overview
--------
What the understanding step returns for one customer message: the intent, the slots it found, the
language it read and how sure it is. The result is the only thing the rest of the system learns
from the message; the text itself is not stored.

Scope
-----
In: the result model, the closed set of intents and the confirmation answers.
Out: producing it (a model adapter or a fake) and acting on it (the dialogue controller).

Design Principles
-----------------
- The result is a strict schema. Output that does not validate is treated as unusable
  understanding: ``NluResult.unusable()`` is that value, so the controller has one path for it.
- Confirmation is a closed set that separates an explicit yes from everything else. Only ``YES``
  can lead to a filing; a yes with a change, a bare "ok" and a doubt are different answers.
- Free text is bounded and stays a value: a merchant, a detail and a policy question are data,
  never instructions to anything downstream, and none may hold a control character or a card
  number.
- A slot belongs to its intent: a confirmation, a choice and a requested language are read only
  for the intent that asks for them.
- Dates arrive already resolved against the reference date the caller supplied, with the way they
  were expressed, so the controller can ask the customer to confirm a resolved date in words.

Runtime Contract
----------------
``NluResult`` with ``NluIntent``, ``ConfirmationAnswer``, ``DateSource`` and ``TransactionHint``.

Limitations
-----------
The confidence is the model's own and is poorly calibrated; a deterministic guard for missing
elements, not this number alone, decides when to ask.
"""

from __future__ import annotations

# Standard libraries
from datetime import date  # A resolved date of a transaction
from decimal import Decimal  # Money is never a float
from enum import StrEnum  # Closed sets of the contract
from typing import Annotated  # Bounded fields

# Third-party libraries
from pydantic import Field, model_validator  # Field bounds and cross-field rules

# Local modules
from app.domain.policy.models import DisputeCategory  # The categories a dispute can have
from contracts.service_v1.envelope import (  # Shared base, types and vocabulary
    ContractModel,
    DateSource,
    Lang,
    Rate,
    SafeText,
)


class NluIntent(StrEnum):
    """What the customer wants from this message."""

    FILE_DISPUTE = "file_dispute"
    LIST_TRANSACTIONS = "list_transactions"
    DISPUTE_STATUS = "dispute_status"
    POLICY_QUESTION = "policy_question"
    CONFIRMATION = "confirmation"
    CHOICE = "choice"
    CORRECTION = "correction"
    REPORT_FRAUD = "report_fraud"
    REPORT_CARD_LOSS = "report_card_loss"
    REQUEST_PERSON = "request_person"
    REQUEST_REVERSAL = "request_reversal"
    UNSUPPORTED_ACTION = "unsupported_action"
    SWITCH_LANGUAGE = "switch_language"
    SMALL_TALK = "small_talk"
    FAREWELL = "farewell"
    UNCLEAR = "unclear"


class ConfirmationAnswer(StrEnum):
    """How the customer answered a pending confirmation prompt."""

    YES = "yes"
    YES_WITH_CHANGE = "yes_with_change"
    AMBIGUOUS = "ambiguous"
    NO = "no"


class TransactionHint(ContractModel):
    """What the customer said about the transaction, in the fields the system searches by."""

    merchant: Annotated[SafeText, Field(min_length=1, max_length=80)] | None = None
    amount: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)] | None = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    date_on: date | None = None
    date_source: DateSource | None = None
    product_last4: Annotated[str, Field(pattern=r"^\d{4}$")] | None = None

    @property
    def is_empty(self) -> bool:
        """Whether the customer gave nothing to search by."""
        return not any(
            value is not None
            for value in (self.merchant, self.amount, self.date_on, self.product_last4)
        )


class NluResult(ContractModel):
    """The understanding of one message."""

    intent: NluIntent
    confidence: Rate
    language: Lang | None = None
    transaction: TransactionHint = TransactionHint()
    category: DisputeCategory | None = None
    detail: Annotated[SafeText, Field(min_length=1, max_length=500)] | None = None
    confirmation: ConfirmationAnswer | None = None
    choice: Annotated[int, Field(ge=1, le=5)] | None = None
    requested_language: Lang | None = None
    policy_query: Annotated[SafeText, Field(min_length=1, max_length=200)] | None = None
    mentions_second_dispute: bool = False

    @model_validator(mode="after")
    def _slots_belong_to_their_intent(self) -> NluResult:
        """A confirmation, a choice and a requested language come only with their own intent."""
        if self.confirmation is not None and self.intent is not NluIntent.CONFIRMATION:
            raise ValueError("confirmation is only read for the confirmation intent")
        if self.choice is not None and self.intent is not NluIntent.CHOICE:
            raise ValueError("choice is only read for the choice intent")
        if self.requested_language is not None and self.intent is not NluIntent.SWITCH_LANGUAGE:
            raise ValueError("requested_language is only read for the switch_language intent")
        return self

    @classmethod
    def unusable(cls) -> NluResult:
        """The result for output that did not validate or did not arrive: nothing understood."""
        return cls(intent=NluIntent.UNCLEAR, confidence=0.0)
