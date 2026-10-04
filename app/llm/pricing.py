"""
Model Pricing
==============

Overview
--------
The per-model price a completion's token accounting is converted into a dollar cost with, so a
log line or a report can state a cost, not just a token count.

Scope
-----
In: the one price table and the one conversion function.
Out: where the cost is logged (the caller's job) or summarized (``app.observability.turn_metrics``).

Design Principles
-----------------
- Money is never a float: the conversion is done in ``Decimal`` throughout.
- The two prices are the pinned Anthropic prices for the supported models, repeated here as the one
  place a cost is computed from them rather than re-derived at each call site.
- The price table's keys are exactly ``app.config.ALLOWED_MODELS``; a model reaching this
  function that is not priced here is a configuration drift between the two, not a normal
  runtime case, so it raises rather than silently costing nothing.

Runtime Contract
----------------
``cost_usd(model, input_tokens, output_tokens) -> Decimal`` raises ``KeyError`` for an unpriced
model id; ``is_priced(model) -> bool`` says whether an id is priced.
"""

from __future__ import annotations

# Standard libraries
from decimal import Decimal  # Money is never a float

_TOKENS_PER_PRICE_UNIT = Decimal(1_000_000)

# (price per million input tokens, price per million output tokens), in USD.
_PRICE_PER_MILLION_TOKENS: dict[str, tuple[Decimal, Decimal]] = {
    "claude-haiku-4-5-20251001": (Decimal("1"), Decimal("5")),
    "claude-sonnet-5": (Decimal("2"), Decimal("10")),
}


def is_priced(model: str) -> bool:
    """Whether ``model`` is one of the ids the price table holds."""
    return model in _PRICE_PER_MILLION_TOKENS


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """The cost of one completion, in USD, at the pinned per-model prices.

    Raises
    ------
    KeyError
        ``model`` is not one of the priced ids; the config allow-list already prevents an
        unpinned model from being used at all, so this only fires if the two ever drift apart.
    """
    input_price, output_price = _PRICE_PER_MILLION_TOKENS[model]
    return (
        Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price
    ) / _TOKENS_PER_PRICE_UNIT
