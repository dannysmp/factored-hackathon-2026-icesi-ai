"""
Handoff Builder Tests
======================

Component: ``app.conversation.handoff``. Hermetic and pure: no store, no clock, no network call.
"""

from __future__ import annotations

# Standard libraries
import re
from datetime import UTC, date, datetime
from typing import Any

# Third-party libraries
import pytest

# Local modules
from app.conversation.handoff import build_packet, mask_customer_id
from app.domain.policy.models import DisputeCategory, ReasonCode
from contracts.service_v1.envelope import Slot
from contracts.service_v1.handoff import ActionRecord, HandoffPacket, HandoffTrigger, OpenQuestion

_REFERENCE_DATE = date(2026, 6, 18)
_CREATED_AT = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)


def _packet(**changes: Any) -> HandoffPacket:
    values: dict[str, Any] = {
        "ticket_ref": "T-100",
        "reference_date": _REFERENCE_DATE,
        "created_at": _CREATED_AT,
        "language": "es",
        "trigger": HandoffTrigger.CUSTOMER_REQUEST,
        "first_name": "Ana",
        "customer_id": "CLI-1234",
        "request_summary": "Wants to speak with a person.",
        "reason_codes": (ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,),
        "policy_version": "2",
    }
    return build_packet(**{**values, **changes})


def test_mask_customer_id_keeps_only_the_last_four_alphanumeric_characters() -> None:
    """A masked identifier never shows more than four characters of the original."""
    assert mask_customer_id("CLI-1234") == "****1234"
    assert mask_customer_id("customer-99") == "****er99"


@pytest.mark.parametrize("customer_id", ["9", "_-", "", "a", "____"])
def test_mask_customer_id_pads_to_the_contracts_minimum_length(customer_id: str) -> None:
    """An identifier with fewer than two alphanumeric characters still satisfies the pattern
    CustomerLabel.masked_id requires, rather than raising when build_packet validates it."""
    masked = mask_customer_id(customer_id)

    assert re.fullmatch(r"\*{4}[A-Za-z0-9]{2,4}", masked)


def test_build_packet_produces_a_valid_handoff_packet() -> None:
    """The minimal required fields alone build a valid packet."""
    packet = _packet()

    assert packet.ticket_ref == "T-100"
    assert packet.customer.first_name == "Ana"
    assert packet.customer.masked_id == "****1234"
    assert packet.trigger is HandoffTrigger.CUSTOMER_REQUEST
    assert packet.evidence.policy_version == "2"
    assert packet.verified_facts == ()
    assert packet.open_questions == ()


def test_needs_language_routing_follows_the_language_not_a_caller_flag() -> None:
    """The flag is computed from the language; there is no parameter that could disagree."""
    assert _packet(language="es").needs_language_routing is False
    assert _packet(language="pt").needs_language_routing is True
    assert _packet(language="en").needs_language_routing is True


def test_build_packet_carries_optional_parts_when_given() -> None:
    """Actions, an attempted action, an existing case and open questions all pass through."""
    packet = _packet(
        category=DisputeCategory.FRAUD_CLAIM,
        actions=(ActionRecord(action="looked_up_transaction", result="found"),),
        attempted_action=ActionRecord(action="create_dispute_case", result="tool_failure"),
        existing_case_number="D-1",
        open_questions=(OpenQuestion(slot=Slot.REASON, attempts=2),),
    )

    assert packet.category is DisputeCategory.FRAUD_CLAIM
    assert packet.actions[0].action == "looked_up_transaction"
    assert packet.attempted_action is not None
    assert packet.attempted_action.result == "tool_failure"
    assert packet.existing_case_number == "D-1"
    assert packet.open_questions[0].slot is Slot.REASON
