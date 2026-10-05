"""
Dialogue State Tests
====================

Component: ``app.conversation.state``. Hermetic and pure: no store, no clock read internally.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.conversation.state import ConversationPhase, DialogueState
from app.domain.policy.models import DisputeCategory
from contracts.service_v1.envelope import Slot

_NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _state(**changes: object) -> DialogueState:
    values: dict[str, object] = {"session_id": "s-1", "lang": "es", "updated_at": _NOW}
    return DialogueState(**{**values, **changes})


def test_a_fresh_state_starts_with_no_pending_slot_and_zero_attempts() -> None:
    """A conversation that has not asked anything yet has nothing pending."""
    state = _state()

    assert state.phase is ConversationPhase.STARTED
    assert state.pending_slot is None
    assert state.clarification_attempts == 0


def test_asking_for_the_same_slot_again_increments_the_counter() -> None:
    """Two consecutive clarifications on the same element count up."""
    first = _state().with_clarification(Slot.REASON)
    second = first.with_clarification(Slot.REASON)

    assert first.clarification_attempts == 1
    assert second.clarification_attempts == 2
    assert second.phase is ConversationPhase.CLARIFYING


def test_asking_for_a_different_slot_resets_the_counter() -> None:
    """A new blocker starts its own count; the old one's attempts do not carry over."""
    twice_on_reason = _state().with_clarification(Slot.REASON).with_clarification(Slot.REASON)

    once_on_transaction = twice_on_reason.with_clarification(Slot.TRANSACTION)

    assert once_on_transaction.pending_slot is Slot.TRANSACTION
    assert once_on_transaction.clarification_attempts == 1


def test_filling_the_pending_slot_clears_it_and_resets_the_counter() -> None:
    """Once answered, nothing is pending and the next clarification starts fresh."""
    clarifying = _state().with_clarification(Slot.REASON).with_clarification(Slot.REASON)

    filled = clarifying.with_slot_filled()

    assert filled.pending_slot is None
    assert filled.clarification_attempts == 0


def test_with_language_changes_only_the_language() -> None:
    """Switching language does not disturb the rest of the state."""
    state = _state().with_clarification(Slot.TRANSACTION)

    switched = state.with_language("pt")

    assert switched.lang == "pt"
    assert switched.pending_slot is Slot.TRANSACTION
    assert switched.clarification_attempts == state.clarification_attempts


def test_with_phase_changes_only_the_phase() -> None:
    """Moving to a terminal phase leaves the rest of the state as it was."""
    state = _state().with_clarification(Slot.REASON)

    handed_off = state.with_phase(ConversationPhase.HANDED_OFF)

    assert handed_off.phase is ConversationPhase.HANDED_OFF
    assert handed_off.pending_slot is Slot.REASON


def test_with_case_filed_closes_the_conversation_and_names_the_case() -> None:
    """Filing a case moves to closed and records the number a replay re-reads from."""
    state = _state().with_clarification(Slot.CONFIRMATION)

    filed = state.with_case_filed("D-1")

    assert filed.phase is ConversationPhase.CLOSED
    assert filed.last_case_number == "D-1"


def test_with_case_filed_leaves_nothing_of_the_filed_dispute_open() -> None:
    """The filing question, the clarification count, the transaction and the reason are cleared."""
    state = _state().model_copy(
        update={"selected_ref": "TX-1", "category": DisputeCategory.UNRECOGNIZED_CHARGE}
    )
    state = state.with_clarification(Slot.CONFIRMATION)

    filed = state.with_case_filed("D-1")

    assert filed.pending_slot is None
    assert filed.clarification_attempts == 0
    assert filed.selected_ref is None
    assert filed.category is None


def test_with_dispute_closed_leaves_nothing_open_and_names_no_case() -> None:
    """A dispute that ends without a case closes the conversation and clears its selections."""
    state = _state().model_copy(
        update={"selected_ref": "TX-1", "category": DisputeCategory.UNRECOGNIZED_CHARGE}
    )
    state = state.with_clarification(Slot.CONFIRMATION)

    closed = state.with_dispute_closed()

    assert closed.phase is ConversationPhase.CLOSED
    assert closed.pending_slot is None
    assert closed.clarification_attempts == 0
    assert closed.selected_ref is None
    assert closed.category is None
    assert closed.last_case_number is None


def test_with_handed_off_moves_to_handed_off_and_names_the_ticket() -> None:
    """A handoff records its ticket reference and moves to the handed-off phase."""
    state = _state()

    handed_off = state.with_handed_off("T-1")

    assert handed_off.phase is ConversationPhase.HANDED_OFF
    assert handed_off.last_ticket_ref == "T-1"


def test_a_state_is_immutable() -> None:
    """Every transition returns a new object; the original is never mutated."""
    state = _state()

    with pytest.raises(ValidationError):
        state.phase = ConversationPhase.CLOSED  # type: ignore[misc]


def test_unknown_fields_and_out_of_range_values_are_refused() -> None:
    """A misspelled key or an invalid count fails at the boundary."""
    with pytest.raises(ValidationError):
        DialogueState(session_id="s-1", lang="es", updated_at=_NOW, extra_field=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        _state(clarification_attempts=-1)
    with pytest.raises(ValidationError):
        _state(pending_disputes=6)


def test_the_reference_instant_must_be_timezone_aware() -> None:
    """A naive datetime is refused, matching every other instant of record in the system."""
    with pytest.raises(ValidationError):
        DialogueState(session_id="s-1", lang="es", updated_at=datetime(2026, 9, 27, 12, 0))
