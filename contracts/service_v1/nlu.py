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
  never instructions to anything downstream, and none may hold a control character. Redacting a
  card number in free text is the masking serializer's job at the egress boundary, not this
  contract's.
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

    FILE_DISPUTE = "file_dispute"  # Wants to dispute a transaction.
    LIST_TRANSACTIONS = "list_transactions"  # Wants to see recent transactions.
    DISPUTE_STATUS = "dispute_status"  # Asks where an existing dispute stands.
    POLICY_QUESTION = "policy_question"  # Asks what the dispute policy allows.
    CONFIRMATION = "confirmation"  # Answers a pending yes/no prompt.
    CHOICE = "choice"  # Picks one of the numbered options shown.
    CORRECTION = "correction"  # Changes something said earlier.
    REPORT_FRAUD = "report_fraud"  # Says the transaction was not theirs.
    REPORT_CARD_LOSS = "report_card_loss"  # Says a card was lost or stolen.
    REQUEST_PERSON = "request_person"  # Asks for a human agent.
    REQUEST_REVERSAL = "request_reversal"  # Asks for money back outside a dispute.
    UNSUPPORTED_ACTION = "unsupported_action"  # Asks for something the service does not do.
    SWITCH_LANGUAGE = "switch_language"  # Asks to continue in another language.
    SMALL_TALK = "small_talk"  # Greets or chats without a request.
    FAREWELL = "farewell"  # Ends the conversation.
    UNCLEAR = "unclear"  # Nothing usable could be read.


class ConfirmationAnswer(StrEnum):
    """How the customer answered a pending confirmation prompt."""

    YES = "yes"  # An unqualified, explicit yes: the only answer that can lead to a filing.
    YES_WITH_CHANGE = "yes_with_change"  # Agrees but also changes something.
    AMBIGUOUS = "ambiguous"  # A bare "ok", a doubt or anything not clearly yes or no.
    NO = "no"  # Declines.


class TransactionHint(ContractModel):
    """What the customer said about the transaction, in the fields the system searches by."""

    # Each field is None when the customer did not say it.
    merchant: Annotated[SafeText, Field(min_length=1, max_length=80)] | None = None
    # The same bound as ``Money``: 12 digits before the point and 2 after, inside the store's
    # ``NUMERIC(15, 2)``. A wider figure fails this field; the extraction repair then drops the
    # amount and keeps the rest of the hint.
    amount: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)] | None = None
    # Three-letter currency code, when the customer named one.
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    # The date resolved against the caller's reference date, and how the customer expressed it.
    date_on: date | None = None
    date_source: DateSource | None = None
    # Last four digits of the card or account, never a longer number.
    product_last4: Annotated[str, Field(pattern=r"^\d{4}$")] | None = None

    @property
    def is_empty(self) -> bool:
        """Whether the customer gave nothing to search by."""
        return not any(
            value is not None
            for value in (self.merchant, self.amount, self.date_on, self.product_last4)
        )

    @model_validator(mode="after")
    def _date_source_pairs_with_a_date(self) -> TransactionHint:
        """A date is read only together with how it was expressed, and never the other way."""
        if (self.date_on is None) != (self.date_source is None):
            raise ValueError("date_on and date_source must be given together or not at all")
        return self


class NluResult(ContractModel):
    """The understanding of one message."""

    intent: NluIntent
    # How sure the understanding step is, from 0 to 1; zero when no usable reading was produced.
    confidence: Rate
    # The language the message was written in, when it could be told.
    language: Lang | None = None
    transaction: TransactionHint = TransactionHint()
    category: DisputeCategory | None = None
    # The customer's reason in a short phrase; data, never an instruction.
    detail: Annotated[SafeText, Field(min_length=1, max_length=500)] | None = None
    # Confirmation, choice and requested language are set only for their own intent.
    confirmation: ConfirmationAnswer | None = None
    choice: Annotated[int, Field(ge=1, le=5)] | None = None
    requested_language: Lang | None = None
    # The customer's policy question, for the policy lookup.
    policy_query: Annotated[SafeText, Field(min_length=1, max_length=200)] | None = None
    # True when the message also raises a second, different dispute.
    mentions_second_dispute: bool = False

    @model_validator(mode="after")
    def _slots_belong_to_their_intent(self) -> NluResult:
        """A confirmation, a choice and a requested language exist exactly for their own intent."""
        if (self.confirmation is not None) != (self.intent is NluIntent.CONFIRMATION):
            raise ValueError("confirmation is read exactly for the confirmation intent")
        if (self.choice is not None) != (self.intent is NluIntent.CHOICE):
            raise ValueError("choice is read exactly for the choice intent")
        if (self.requested_language is not None) != (self.intent is NluIntent.SWITCH_LANGUAGE):
            raise ValueError("requested_language is read exactly for the switch_language intent")
        return self

    @classmethod
    def unusable(cls) -> NluResult:
        """The result for output that did not validate or did not arrive: nothing understood."""
        return cls(intent=NluIntent.UNCLEAR, confidence=0.0)
