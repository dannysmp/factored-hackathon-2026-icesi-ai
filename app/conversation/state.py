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
- The clarification counter is one integer bound to whichever slot is currently pending. It counts
  the answers that left the question unsettled: the first time a slot is asked it is zero, asking
  again for the same pending slot increments it, and asking for a different slot or filling the
  pending one resets it to zero. This is the missing-slot guard the architecture describes: it
  counts consecutive unsettled answers on one element, whatever the reported confidence.
- A new login starts a new conversation (AC-E5-60): this model carries no notion of "resume", and
  the store is what would have to go out of its way to look up a stale session by a new one's id,
  which it never does.
- Optimistic concurrency: a state carries the version it was read at; the store turns a stale
  write into a conflict rather than a silent overwrite.
- Immutable: every transition returns a new state, so a caller can never share and mutate one
  across concurrent turns by accident.

Runtime Contract
----------------
``DialogueState`` with ``with_clarification(slot)``, ``with_slot_filled()``, ``with_case_filed(
case_number)`` and ``with_handed_off(ticket_ref)``, plus ``is_opening``, which is true while no
dispute step has been taken yet. ``ConversationPhase`` names where the conversation stands.

Limitations
-----------
``last_case_number``/``last_ticket_ref`` exist so a repeated turn id can be answered from the
state alone (no cached reply text is stored, per the store's own idempotent-replay design): the
caller re-derives the reply from the current record behind the identifier, never from a snapshot
taken when it was first written.
"""

from __future__ import annotations

# Standard libraries
from enum import StrEnum  # Closed set of conversation phases
from typing import Annotated  # Bounded fields

# Third-party libraries
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field  # Validated immutable models

# Local modules
from app.domain.policy.models import DisputeCategory  # The category a dispute falls under
from contracts.service_v1.envelope import Lang, Slot  # Shared vocabulary


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
    last_case_number: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    last_ticket_ref: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    updated_at: AwareDatetime

    @property
    def turns_applied(self) -> int:
        """Customer turns applied to this session so far.

        Equal to ``version``: the store starts a session at version 1 on its first applied turn
        and advances it by exactly one per applied turn; a replayed or conflicting turn changes
        nothing.
        """
        return self.version

    @property
    def is_opening(self) -> bool:
        """True while no dispute step has been taken: the conversation has had at least one turn
        but is still in its first phase with no slot being asked, no category and no transaction
        chosen. Informational turns (a greeting, a policy answer, a transaction list) leave it
        true, so the language the conversation continues in may still be set by a later message."""
        return (
            self.last_turn_id is not None
            and self.phase is ConversationPhase.STARTED
            and self.pending_slot is None
            and self.category is None
            and self.selected_ref is None
        )

    def with_clarification(self, slot: Slot) -> DialogueState:
        """Ask for ``slot``.

        The count is of answers that left the question unsettled. It grows by one when the slot
        is the one already pending (the customer was asked and did not answer it), and starts at 0
        when a different slot becomes the blocker: a question asked for the first time has not yet
        been answered badly, and a filled slot is never re-asked with a stale count from something
        else.
        """
        attempts = self.clarification_attempts + 1 if slot == self.pending_slot else 0
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

    def with_case_filed(self, case_number: str) -> DialogueState:
        """A case was filed this turn: closed, with the case number a replay re-reads from."""
        return self.model_copy(
            update={"phase": ConversationPhase.CLOSED, "last_case_number": case_number}
        )

    def with_handed_off(self, ticket_ref: str) -> DialogueState:
        """The conversation was handed to a person: nothing about the ticket changes on replay."""
        return self.model_copy(
            update={"phase": ConversationPhase.HANDED_OFF, "last_ticket_ref": ticket_ref}
        )
