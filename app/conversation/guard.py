"""
Deterministic Missing-Slot Guard
==================================

Overview
--------
Decides whether a request to file a dispute is still missing an element it needs, from what this
message's understanding carried and what the conversation already has — never from the model's own
confidence. AC-E5-58: a required element missing asks the customer, whatever confidence the model
reports; the NLU confidence floor is a separate, model-quality concern the policy engine checks on
its own (``nlu_confidence_floor``), not this guard's job.

Scope
-----
In: the pure decision of which slot, if any, is still missing.
Out: acting on the answer — asking again, counting the attempt, and escalating at the shared
budget — is the dialogue controller's job (``DialogueState.with_clarification``,
``with_slot_filled``; ``Policy.routing.clarification_budget`` for K), wired in the turns endpoint.

Design Principles
-----------------
- Only filing a dispute has an open-ended gap the understanding step alone cannot always close: a
  transaction to search by, and a reason once one is selected. Every other intent's required slot
  is a contract-level rule already: ``NluResult`` refuses a ``confirmation``, a ``choice`` or a
  ``requested_language`` that does not belong to its own intent, so those never reach here missing.
- Reads conversation state as well as the newest understanding, because a transaction or a reason
  given in an earlier turn still satisfies the requirement now; a customer is never asked again for
  something they already gave.
- Pure function, no side effect: the caller decides what "missing" means for the counter it keeps.

Runtime Contract
----------------
``required_slot(result, state) -> Slot | None``.
"""

from __future__ import annotations

# Local modules
from app.conversation.state import DialogueState  # What the conversation already has
from contracts.service_v1.envelope import Slot  # The element being asked for
from contracts.service_v1.nlu import NluIntent, NluResult  # This message's understanding


def required_slot(result: NluResult, state: DialogueState) -> Slot | None:
    """The slot still missing to act on ``result``, given ``state``, or ``None`` if nothing is.

    Parameters
    ----------
    result : NluResult
        The newest message's understanding.
    state : DialogueState
        The conversation's state before this message.

    Returns
    -------
    Slot | None
        ``Slot.TRANSACTION`` when no transaction is selected yet and this message named none to
        search by; ``Slot.REASON`` when a transaction is selected (or this message gives one) but
        no category is known yet and this message names none; ``None`` when the request is not a
        filing at all, or already carries everything it needs.
    """
    if result.intent is not NluIntent.FILE_DISPUTE:
        return None
    if state.selected_ref is None and result.transaction.is_empty:
        return Slot.TRANSACTION
    if state.category is None and result.category is None:
        return Slot.REASON
    return None
