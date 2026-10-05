"""
Daily Model Spend Breaker
=========================

Overview
--------
A guard around the language-model port that refuses further calls once the day's recorded model
spend reaches a configured limit, so no single day can exhaust the provider's monthly limit. A
refused call raises ``LlmSpendLimitReached``, an ``LlmUnavailable``: every caller already degrades
that to a template reply and a handoff to a person, so a customer is never left without an answer.

Scope
-----
In: the ledger port, the gate that charges and checks it, and the error it raises.
Out: the Postgres ledger (``app.persistence.spend_ledger``) and the setting that carries the limit
(``app.config.Settings.llm_daily_spend_limit_usd``).

Design Principles
-----------------
- One seam: every paid call, understanding and rendering alike, crosses the client port, so the
  gate charges there and no call can be missed.
- The cost is the existing per-model price conversion (``app.llm.pricing.cost_usd``) applied to the
  result's own token accounting, not a second accounting. The model priced is the one the provider
  reports when that id is in the price table, otherwise the one requested: a provider reporting a
  dated alias of a priced model must still be charged, not skipped.
- The operating day comes from an injected clock, read in the bank's fixed operating zone.
- Fail closed: if today's total cannot be read, the call is refused. A failed charge after a call
  that already happened is logged and never blocks the reply the call produced.
- A call already in flight when the limit is crossed completes, so concurrent calls can overshoot
  the limit by a call or two. The limit is a guard under the provider's own hard monthly limit.

Limitations
-----------
- The total covers completed calls the service makes for customers. A call that fails after the
  provider has billed it returns no token accounting and is not recorded, and the offline
  evaluation runs build their own clients and spend outside it. It is a lower bound on the
  provider's bill, not a reconciliation of it.

Runtime Contract
----------------
``SpendLedger`` (protocol): ``spent(day) -> Decimal``, ``add(day, usd)``.
``SpendGatedLlmClient(inner, ledger, limit_usd, clock).complete(request)``: as the inner client's,
raising ``LlmSpendLimitReached`` instead of calling it once the day's spend is at or over the limit.
"""

from __future__ import annotations

# Standard libraries
import logging  # Structured events
from dataclasses import dataclass  # The gate's collaborators
from datetime import date  # The operating day
from decimal import Decimal  # Money is never a float
from typing import Protocol  # The ledger port

# Local modules
from app.domain.calendar import BANK_ZONE  # The bank's operating zone
from app.llm.client import CompletionRequest, CompletionResult, LlmClient, LlmUnavailable
from app.llm.pricing import cost_usd, is_priced  # The one cost conversion
from app.security.sessions import Clock  # Injected time

logger = logging.getLogger(__name__)


class LlmSpendLimitReached(LlmUnavailable):
    """The day's model spend is at the limit, or could not be read; no call was made."""


class SpendLedger(Protocol):
    """Where the day's model spend is kept."""

    def spent(self, day: date) -> Decimal:
        """The dollars recorded for ``day``; zero when nothing has been charged."""
        ...

    def add(self, day: date, usd: Decimal) -> None:
        """Add ``usd`` to ``day``'s total, atomically."""
        ...


@dataclass(frozen=True, slots=True)
class SpendGatedLlmClient:
    """An ``LlmClient`` that stops calling the model once the day's spend reaches the limit."""

    inner: LlmClient
    ledger: SpendLedger
    limit_usd: Decimal
    clock: Clock

    def complete(self, request: CompletionRequest) -> CompletionResult:
        """Raises ``LlmSpendLimitReached`` when the limit is reached or the total is unreadable;
        otherwise as the inner client's own ``complete`` does."""
        day = self.clock().astimezone(BANK_ZONE).date()
        try:
            spent = self.ledger.spent(day)
        except Exception:
            logger.exception("daily_spend_unreadable day=%s", day.isoformat())
            raise LlmSpendLimitReached("the day's model spend could not be read") from None
        if spent >= self.limit_usd:
            logger.warning(
                "daily_spend_limit_reached day=%s spent_usd=%s limit_usd=%s",
                day.isoformat(),
                spent,
                self.limit_usd,
            )
            raise LlmSpendLimitReached("the day's model spend limit is reached")
        result = self.inner.complete(request)
        priced_model = result.model if is_priced(result.model) else request.model
        try:
            self.ledger.add(day, cost_usd(priced_model, result.input_tokens, result.output_tokens))
        except Exception:
            logger.exception(
                "daily_spend_charge_failed day=%s model=%s", day.isoformat(), priced_model
            )
        return result
