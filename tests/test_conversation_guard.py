"""
Deterministic Missing-Slot Guard Tests
=========================================

Component: ``app.conversation.guard``. Hermetic and pure: no store, no model call, no clock read
internally. Covers AC-E5-58: a required element missing asks the customer whatever confidence the
model reports, so every case here uses a high confidence to keep the point visible.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.conversation.guard import required_slot
from app.conversation.state import DialogueState
from app.domain.policy.models import DisputeCategory
from contracts.service_v1.envelope import Slot
from contracts.service_v1.nlu import ConfirmationAnswer, NluIntent, NluResult, TransactionHint

_NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

# The companion slot each intent's own contract validator requires it to carry, so a loop over
# every intent can build a valid result without also being a filing.
_COMPANION_FIELDS: dict[NluIntent, dict[str, object]] = {
    NluIntent.CONFIRMATION: {"confirmation": ConfirmationAnswer.YES},
    NluIntent.CHOICE: {"choice": 1},
    NluIntent.SWITCH_LANGUAGE: {"requested_language": "pt"},
}


def _state(**changes: object) -> DialogueState:
    values: dict[str, object] = {"session_id": "s-1", "lang": "es", "updated_at": _NOW}
    return DialogueState(**{**values, **changes})


def _result(intent: NluIntent, **changes: object) -> NluResult:
    values: dict[str, object] = {
        "intent": intent,
        "confidence": 0.97,
        **_COMPANION_FIELDS.get(intent, {}),
    }
    return NluResult(**{**values, **changes})


def test_a_non_filing_intent_is_never_missing_a_slot() -> None:
    """The guard only ever names a slot for a filing; every other intent returns nothing missing."""
    for intent in NluIntent:
        if intent is NluIntent.FILE_DISPUTE:
            continue
        result = _result(intent)

        assert required_slot(result, _state()) is None


def test_a_filing_with_no_transaction_hint_and_none_selected_needs_the_transaction() -> None:
    """Nothing to search by, and nothing already picked: the transaction is what is missing."""
    result = _result(NluIntent.FILE_DISPUTE, transaction=TransactionHint())

    assert required_slot(result, _state()) is Slot.TRANSACTION


def test_high_confidence_does_not_excuse_a_missing_transaction() -> None:
    """AC-E5-58: the guard asks whatever confidence the model reports."""
    result = _result(NluIntent.FILE_DISPUTE, confidence=0.99, transaction=TransactionHint())

    assert required_slot(result, _state()) is Slot.TRANSACTION


def test_a_transaction_already_selected_in_state_satisfies_the_requirement() -> None:
    """A transaction picked in an earlier turn is not asked for again."""
    result = _result(NluIntent.FILE_DISPUTE, transaction=TransactionHint())
    state = _state(selected_ref="txn-1")

    assert required_slot(result, state) is not Slot.TRANSACTION


def test_a_transaction_hint_in_this_message_satisfies_the_requirement() -> None:
    """A merchant named in the newest message is enough, without needing prior state."""
    result = _result(NluIntent.FILE_DISPUTE, transaction=TransactionHint(merchant="Acme"))

    assert required_slot(result, _state()) is not Slot.TRANSACTION


def test_a_selected_transaction_but_no_category_needs_the_reason() -> None:
    """Once the transaction is settled, the reason is the next thing that can be missing."""
    result = _result(NluIntent.FILE_DISPUTE, transaction=TransactionHint())
    state = _state(selected_ref="txn-1")

    assert required_slot(result, state) is Slot.REASON


def test_a_category_already_known_in_state_satisfies_the_requirement() -> None:
    """A category recorded in an earlier turn is not asked for again."""
    result = _result(NluIntent.FILE_DISPUTE, transaction=TransactionHint())
    state = _state(selected_ref="txn-1", category=DisputeCategory.UNRECOGNIZED_CHARGE)

    assert required_slot(result, state) is None


def test_a_category_given_in_this_message_satisfies_the_requirement() -> None:
    """A reason named in the same message as the transaction closes both requirements at once."""
    result = _result(
        NluIntent.FILE_DISPUTE,
        transaction=TransactionHint(merchant="Acme"),
        category=DisputeCategory.UNRECOGNIZED_CHARGE,
    )

    assert required_slot(result, _state()) is None


def test_everything_already_known_leaves_nothing_missing() -> None:
    """Transaction and category both already settled in state: the filing can proceed."""
    result = _result(NluIntent.FILE_DISPUTE, transaction=TransactionHint())
    state = _state(selected_ref="txn-1", category=DisputeCategory.UNRECOGNIZED_CHARGE)

    assert required_slot(result, state) is None
