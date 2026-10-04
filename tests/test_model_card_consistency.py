"""
Model Card / Policy Consistency Test
======================================

Component: the boundary between `models/model_card.json` and the shipped policy's own
`risk_routing_enabled` and `risk_score_threshold`. Not hermetic by construction: the whole point is
that the two committed files agree as they ship, so these tests read both of them directly rather
than a handmade fixture of either.
"""

from __future__ import annotations

# Standard libraries
import json  # Reading the shipped model card
from pathlib import Path  # Its location, next to the experiment log

# Local modules
from app.domain.policy import DEFAULT_POLICY_PATH, load_policy

MODEL_CARD_PATH = Path(__file__).resolve().parents[1] / "models" / "model_card.json"


def _shipped_card() -> dict[str, object]:
    """The shipped model card as plain data."""
    loaded = json.loads(MODEL_CARD_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_the_model_card_exists_and_is_machine_readable() -> None:
    card = _shipped_card()
    assert isinstance(card["routing_enabled"], bool)
    assert "threshold" in card
    assert isinstance(card["selected_model"], str)


def test_routing_enabled_matches_between_the_card_and_the_policy() -> None:
    card = _shipped_card()
    policy = load_policy(DEFAULT_POLICY_PATH)
    assert policy.routing.risk_routing_enabled == card["routing_enabled"]


def test_a_card_with_no_threshold_means_the_policy_switch_is_off() -> None:
    """No threshold cleared the floor, so routing stays off in the policy too."""
    card = _shipped_card()
    if card["threshold"] is None:
        policy = load_policy(DEFAULT_POLICY_PATH)
        assert policy.routing.risk_routing_enabled is False
        assert card["routing_enabled"] is False


def test_a_card_with_a_threshold_means_the_policy_threshold_equals_it() -> None:
    """With a threshold chosen, the policy's own value must equal the card's."""
    card = _shipped_card()
    threshold = card["threshold"]
    if threshold is not None:
        assert isinstance(threshold, dict)
        policy = load_policy(DEFAULT_POLICY_PATH)
        assert policy.routing.risk_score_threshold == threshold["threshold"]
        assert policy.routing.risk_routing_enabled == card["routing_enabled"]
