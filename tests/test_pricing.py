"""
Model Pricing Tests
=====================

Component: ``app.llm.pricing``. Hermetic: pure arithmetic, no network.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import ALLOWED_MODELS
from app.llm.pricing import _PRICE_PER_MILLION_TOKENS, cost_usd


def test_every_allowed_model_is_priced() -> None:
    """A model reaching the config allow-list without a price here would cost nothing, silently."""
    assert set(_PRICE_PER_MILLION_TOKENS) == set(ALLOWED_MODELS)


def test_the_cost_of_a_million_input_tokens_is_the_pinned_input_price() -> None:
    assert cost_usd("claude-haiku-4-5-20251001", 1_000_000, 0) == Decimal("1")
    assert cost_usd("claude-sonnet-5", 1_000_000, 0) == Decimal("2")


def test_the_cost_of_a_million_output_tokens_is_the_pinned_output_price() -> None:
    assert cost_usd("claude-haiku-4-5-20251001", 0, 1_000_000) == Decimal("5")
    assert cost_usd("claude-sonnet-5", 0, 1_000_000) == Decimal("10")


def test_input_and_output_tokens_are_priced_independently_and_summed() -> None:
    assert cost_usd("claude-haiku-4-5-20251001", 100, 20) == Decimal("0.0002")


def test_zero_tokens_cost_nothing() -> None:
    assert cost_usd("claude-sonnet-5", 0, 0) == Decimal("0")


def test_an_unpriced_model_raises_rather_than_costing_nothing() -> None:
    with pytest.raises(KeyError):
        cost_usd("claude-opus-4", 100, 100)
