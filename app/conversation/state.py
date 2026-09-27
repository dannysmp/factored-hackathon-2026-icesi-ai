"""
Dialogue State
==============

Overview
--------
The structured state one conversation keeps between turns: identifiers, the slots collected so
far, the pending filing, the clarification count and the language. No raw message text is ever
part of it (AC-E5-57): the customer's own words are read once by the understanding step and never
stored here.

Scope
-----
In: the state model and its pure transitions (asking for a slot again, a slot being filled).
Out: storing it (``store``), understanding a message (``understanding``) and rendering a reply
(``renderer``); the tool-calling steps of locating a transaction and filing a dispute, which need
the scoped tools of the service layer and are wired in a later change.

Design Principles
-----------------
- Structured state only, masked, the same shape discipline as the envelope's own facts.
- The clarification counter is one integer bound to whichever slot is currently pending; asking
  again for the same slot increments it, asking for a different one resets it to 1, and filling
  the pending slot resets it to zero. This is the missing-slot guard the architecture describes:
  it counts consecutive attempts on one element, whatever the reported confidence.
- A new login starts a new conversation (AC-E5-60): this model carries no notion of "resume", and
  the store is what would have to go out of its way to look up a stale session by a new one's id,
  which it never does.
- Optimistic concurrency: a state carries the version it was read at; the store turns a stale
  write into a conflict rather than a silent overwrite.
- Immutable: every transition returns a new state, so a caller can never share and mutate one
  across concurrent turns by accident.

Runtime Contract
----------------
``DialogueState`` with ``with_clarification(slot)`` and ``with_slot_filled()``.
``ConversationPhase`` names where the conversation stands.

Limitations
-----------
Locating a transaction and filing a dispute are not yet phases below: they need the scoped tools
the service layer provides, added when that dependency lands.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.domain.policy.models import DisputeCategory
from contracts.service_v1.envelope import Lang, Slot


class ConversationPhase(StrEnum):
    """Where the conversation stands, between turns."""

    STARTED = "started"
    CLARIFYING = "clarifying"
    CONFIRMING = "confirming"
    CLOSED = "closed"
    HANDED_OFF = "handed_off"
    ABANDONED = "abandoned"


class DialogueState(BaseModel):
    """The structured state of one conversation, keyed by its session id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: Annotated[str, Field(min_length=1, max_length=64)]
    version: Annotated[int, Field(ge=1)] = 1
    lang: Lang
    phase: ConversationPhase = ConversationPhase.STARTED
    pending_slot: Slot | None = None
    clarification_attempts: Annotated[int, Field(ge=0)] = 0
    category: DisputeCategory | None = None
    selected_ref: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    pending_disputes: Annotated[int, Field(ge=0, le=5)] = 0
    last_turn_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    updated_at: AwareDatetime

    def with_clarification(self, slot: Slot) -> DialogueState:
        """Ask for ``slot`` again.

        The attempt count carries over when it is the same slot already pending, and starts at 1
        when a different slot becomes the blocker (a filled slot is never re-asked with a stale
        count from something else).
        """
        attempts = self.clarification_attempts + 1 if slot == self.pending_slot else 1
        return self.model_copy(
            update={
                "phase": ConversationPhase.CLARIFYING,
                "pending_slot": slot,
                "clarification_attempts": attempts,
            }
        )

    def with_slot_filled(self) -> DialogueState:
        """The pending slot was answered: nothing is pending and the counter resets."""
        return self.model_copy(update={"pending_slot": None, "clarification_attempts": 0})

    def with_language(self, lang: Lang) -> DialogueState:
        """The conversation continues in ``lang``."""
        return self.model_copy(update={"lang": lang})

    def with_phase(self, phase: ConversationPhase) -> DialogueState:
        """Move to ``phase`` without touching anything else."""
        return self.model_copy(update={"phase": phase})
