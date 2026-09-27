"""
Golden Set Case Schema Tests
============================

Component: ``evals.models``. Hermetic: every case is built in memory. The adversarial/safe-behavior
pairing rule gets its own isolated tests on both sides, since a schema mistake there would let an
authored case silently score against the wrong rubric.
"""

from __future__ import annotations

# Standard libraries
from typing import Any

# Third-party libraries
import pytest

# Local modules
from contracts.service_v1.envelope import Intent
from evals.models import Case, CaseCategory, SafeBehavior


def _case(**overrides: Any) -> Case:
    """A normal, well-formed case; overrides narrow it to what a test needs."""
    defaults: dict[str, Any] = {
        "case_id": "ES-NORMAL-001",
        "category": CaseCategory.NORMAL,
        "lang": "es",
        "provenance": "observed",
        "seed_ref": "ops_seed:transactions:12345",
        "user_turns": ("No reconozco un cargo en mi tarjeta.",),
        "expected_intent": Intent.CONFIRM_FILING,
    }
    return Case(**{**defaults, **overrides})


# -----------------------------------------------------------------------------
# Required fields
# -----------------------------------------------------------------------------


def test_case_id_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="case_id"):
        _case(case_id="")


def test_user_turns_must_hold_at_least_one_turn() -> None:
    with pytest.raises(ValueError, match="user_turns"):
        _case(user_turns=())


# -----------------------------------------------------------------------------
# The adversarial / safe-behavior pairing rule
# -----------------------------------------------------------------------------


def test_an_adversarial_case_requires_a_safe_behavior() -> None:
    with pytest.raises(ValueError, match="adversarial case must declare"):
        _case(
            case_id="ES-ADV-001",
            category=CaseCategory.ADVERSARIAL,
            expected_intent=Intent.REFUSE,
        )


def test_a_non_adversarial_case_forbids_a_safe_behavior() -> None:
    with pytest.raises(ValueError, match="adversarial-only"):
        _case(expected_safe_behavior=SafeBehavior.REFUSE)


def test_an_adversarial_case_with_a_safe_behavior_is_valid() -> None:
    case = _case(
        case_id="ES-ADV-001",
        category=CaseCategory.ADVERSARIAL,
        expected_intent=Intent.REFUSE,
        expected_safe_behavior=SafeBehavior.REFUSE,
    )
    assert case.is_adversarial
    assert case.expected_safe_behavior is SafeBehavior.REFUSE


def test_a_normal_case_without_a_safe_behavior_is_valid() -> None:
    case = _case()
    assert not case.is_adversarial
    assert case.expected_safe_behavior is None


# -----------------------------------------------------------------------------
# Optional fields
# -----------------------------------------------------------------------------


def test_expected_reason_code_defaults_to_none_for_a_non_decision_case() -> None:
    case = _case(expected_intent=Intent.POLICY_ANSWER)
    assert case.expected_reason_code is None


def test_description_defaults_to_the_empty_string() -> None:
    case = _case()
    assert case.description == ""
