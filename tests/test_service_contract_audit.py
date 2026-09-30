"""
Audit Record Contract Tests
=============================

Component: ``contracts.service_v1.audit``. Hermetic and pure: the contracts are declarative
models, so the tests check what they accept, what they refuse, and what they cannot carry.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.domain.policy.models import ReasonCode
from contracts.service_v1.audit import AuditAction, AuditRecord


def _record(**overrides: object) -> AuditRecord:
    fields: dict[str, object] = {
        "trace_id": "TRACE-1",
        "customer_id": "C1",
        "session_id": "SESSION-1",
        "action": AuditAction.TRANSACTION_VIEWED,
        "tool_result_hash": "a" * 64,
        "occurred_at": datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
        "domain_date": date(2026, 1, 10),
    }
    fields.update(overrides)
    return AuditRecord(**fields)


def test_a_plain_read_carries_no_reason_code_or_policy_version() -> None:
    """Reading the customer's own data needs no policy basis to record."""
    record = _record()

    assert record.reason_code is None
    assert record.policy_version is None


def test_a_policy_decision_records_its_reason_and_version() -> None:
    """An action a policy decision produced carries the reason code and the version behind it."""
    record = _record(
        action=AuditAction.CASE_CREATED, reason_code=ReasonCode.ELIGIBLE, policy_version="1"
    )

    assert record.reason_code is ReasonCode.ELIGIBLE
    assert record.policy_version == "1"


def test_who_acted_is_always_present() -> None:
    """Every record identifies both the customer and the session, never neither."""
    with pytest.raises(ValidationError):
        _record(customer_id="")


def test_a_customer_id_holding_a_control_character_is_refused() -> None:
    """Who acted is system-held text too: it refuses a control character like every other."""
    with pytest.raises(ValidationError, match="control or formatting character"):
        _record(customer_id="C1\x00")


def test_a_session_id_holding_a_control_character_is_refused() -> None:
    """The session identifier is refused the same way as the customer identifier."""
    with pytest.raises(ValidationError, match="control or formatting character"):
        _record(session_id="SESSION\x00")


def test_a_tool_result_hash_must_be_a_lowercase_hex_digest() -> None:
    """The result is identified by its hash, never by its content."""
    with pytest.raises(ValidationError):
        _record(tool_result_hash="not-a-hash")


def test_a_naive_occurred_at_is_refused() -> None:
    """The real instant of the action must carry a timezone; a naive instant is refused."""
    with pytest.raises(ValidationError):
        _record(occurred_at=datetime(2026, 1, 10, 12, 0))


def test_an_audit_record_rejects_an_unknown_field() -> None:
    """The contract is closed: a misspelled key fails at the boundary."""
    with pytest.raises(ValidationError):
        _record(actor="unexpected")


def test_an_audit_record_is_immutable() -> None:
    """A written record cannot be edited after the fact, in code as at the store."""
    record = _record()

    with pytest.raises(ValidationError):
        record.reason_code = ReasonCode.ELIGIBLE  # type: ignore[misc]


@pytest.mark.parametrize(
    "action", [AuditAction.PACKET_VIEWED, AuditAction.TIMELINE_VIEWED], ids=lambda a: a.value
)
def test_an_agent_console_read_carries_no_reason_code_or_policy_version(
    action: AuditAction,
) -> None:
    """Opening a packet or a timeline is a plain read, like any other (ADR-17): no policy basis
    to record, same as a customer's own transaction or case read."""
    record = _record(action=action)

    assert record.reason_code is None
    assert record.policy_version is None


def test_agent_id_defaults_to_none_for_a_customer_originated_action() -> None:
    """A customer action names no agent: session_id alone already identifies the actor."""
    record = _record()

    assert record.agent_id is None


def test_an_agent_action_names_the_agent_who_acted() -> None:
    """ADR-17: an agent read or write is audited with the agent's own durable identity, not only
    the session that carried it."""
    record = _record(action=AuditAction.PACKET_VIEWED, agent_id="AGT-1")

    assert record.agent_id == "AGT-1"


def test_an_agent_id_holding_a_control_character_is_refused() -> None:
    """The agent identifier is system-held text too, refused the same way as every other."""
    with pytest.raises(ValidationError, match="control or formatting character"):
        _record(agent_id="AGT\x00")
