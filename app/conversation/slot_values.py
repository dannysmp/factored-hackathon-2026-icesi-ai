"""
Slot Values
===========

Overview
--------
Turns one envelope's own ``facts``, ``decisions`` and ``sources`` into the grounded
``SlotValues`` a model-rendered reply may cite by field name. Every value is read from the same
place the template renderer reads it from, and formatted with the template renderer's own
``format_money``/``format_date``, so a figure can never drift between what the two rendering paths
would state for the same envelope.

Scope
-----
In: ``slot_values_for(envelope) -> SlotValues``, one builder per ``Intent``.
Out: deciding which intents are eligible for model rendering at all (``app.conversation.reply``),
running the model call (``app.conversation.model_renderer``) and the verification algorithm
(``app.conversation.verifier``).

Design Principles
-----------------
- One direction only, pure: no I/O, no clock, no randomness — every value already lives on the
  envelope this function receives, matching ``app.conversation.facts``'s own translation-only
  design.
- A field the envelope's own facts repeat (several cases, several transactions) becomes one
  ``SlotValue`` entry per occurrence, in the same order the facts list them, so successive
  placeholder occurrences for that field pair with successive facts (``SlotValues``'s own
  documented consumption order).
- No entry is built for a field the calling intent does not allow: a builder here only ever
  produces fields ``INTENT_ALLOWED_FIELDS`` already lists for that intent, so a stray extra entry
  can never let a reply cite something it shouldn't — the verifier enforces the same bound
  independently, this is belt and suspenders, not the only guard.
- Reuses the template renderer's own tables (``CATEGORY_NAMES``, ``INELIGIBLE_TEXT``) instead of a
  second, independently maintained copy of the same wording.

Runtime Contract
----------------
``slot_values_for(envelope: RenderEnvelope) -> SlotValues``.

Limitations
-----------
``OUTCOME_STATEMENT`` for ``Intent.HANDOFF`` is always the one generic "a person will review this"
sentence: nothing in ``RenderEnvelope`` (as opposed to the agent-only ``Envelope``) distinguishes a
fraud escalation from an ordinary one, and some handoff templates (a card-loss report, a customer's
own request) are contractually never model-rendered at all (see
``app.conversation.reply.MODEL_ELIGIBLE_TEMPLATES``) precisely because their wording is
safety-relevant, not a review notice a generic sentence could stand in for. ``OUTCOME_STATEMENT``
for ``Intent.DISPUTE_STATUS`` is the same shape: one of exactly two fixed sentences (a case exists,
or none does), not a per-case summary — every ``INTENT_REQUIRED_FIELDS`` entry only guarantees a
field is *present*, never that its wording adapts to the specific facts beyond what the field's own
name promises.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Callable  # Type of one intent's slot-value builder

# Local modules
from app.conversation.renderer import (  # Reuse the template path's own tables and formatting
    CATEGORY_NAMES,
    INELIGIBLE_TEXT,
    amount_text,
    format_date,
)
from app.domain.policy.models import Outcome
from contracts.service_v1.envelope import (  # The envelope and its typed facts
    GroundedField,
    Intent,
    RenderEnvelope,
)
from contracts.service_v1.verification import SlotValue, SlotValues

# The generic escalation sentence every model-eligible handoff states, matching the first sentence
# of the template renderer's own ``_handoff_review`` (the ticket reference itself is a separate,
# directly-cited field, never folded into this one).
_HANDOFF_OUTCOME_STATEMENT: dict[str, str] = {
    "es": "Un asesor debe revisar esto.",
    "pt": "Um atendente precisa analisar isso.",
    "en": "A person must review this.",
}

# The generic sentence a model-eligible dispute_status reply always states, whether or not a case
# exists, matching the template renderer's own ``_dispute_status`` intro and ``_no_case_found``
# text: the one grounded field every state of the intent carries, since the case-specific fields
# (case_number, case_status, filed_on) exist only when a case does.
_DISPUTE_STATUS_OUTCOME_STATEMENT: dict[str, str] = {
    "es": "Estos son sus casos recientes.",
    "pt": "Estes são seus casos recentes.",
    "en": "Here are your recent cases.",
}
_NO_CASE_FOUND_OUTCOME_STATEMENT: dict[str, str] = {
    "es": "No encontré ningún caso con esos datos en su cuenta.",
    "pt": "Não encontrei nenhum caso com esses dados na sua conta.",
    "en": "I couldn't find a case matching that on your account.",
}


def _present_transactions(e: RenderEnvelope) -> tuple[SlotValue, ...]:
    """The amount, date and merchant of each transaction on offer, as the reply may state them."""
    entries: list[SlotValue] = []
    for transaction in e.facts.transactions:
        entries.append(
            SlotValue(field=GroundedField.AMOUNT, value=amount_text(transaction.amount, e.lang))
        )
        entries.append(
            SlotValue(
                field=GroundedField.OCCURRED_ON, value=format_date(transaction.occurred_on, e.lang)
            )
        )
        if transaction.merchant:
            entries.append(SlotValue(field=GroundedField.MERCHANT, value=transaction.merchant))
    return tuple(entries)


def _confirm_filing(e: RenderEnvelope) -> tuple[SlotValue, ...]:
    """The amount and date of the chosen transaction and the category the reply may state."""
    facts = e.facts
    transaction = next(t for t in facts.transactions if t.ref == facts.selected_ref)
    entries = [
        SlotValue(field=GroundedField.AMOUNT, value=amount_text(transaction.amount, e.lang)),
        SlotValue(
            field=GroundedField.OCCURRED_ON, value=format_date(transaction.occurred_on, e.lang)
        ),
    ]
    if facts.category is not None:
        entries.append(
            SlotValue(field=GroundedField.CATEGORY, value=CATEGORY_NAMES[e.lang][facts.category])
        )
    return tuple(entries)


def _filing_result(e: RenderEnvelope) -> tuple[SlotValue, ...]:
    """The case number, and the date a first response is expected when there is one."""
    case = e.facts.cases[0]
    entries = [SlotValue(field=GroundedField.CASE_NUMBER, value=case.case_number)]
    if case.expected_response_on is not None:
        entries.append(
            SlotValue(
                field=GroundedField.EXPECTED_RESPONSE_ON,
                value=format_date(case.expected_response_on, e.lang),
            )
        )
    return tuple(entries)


def _ineligible(e: RenderEnvelope) -> tuple[SlotValue, ...]:
    """The customer-facing statement of why the dispute cannot be filed."""
    decision = next(d for d in e.decisions if d.outcome is Outcome.INELIGIBLE)
    statement = INELIGIBLE_TEXT[e.lang][decision.customer_reason]
    return (SlotValue(field=GroundedField.OUTCOME_STATEMENT, value=statement),)


def _dispute_status(e: RenderEnvelope) -> tuple[SlotValue, ...]:
    """The number, status and filing date of each case, followed by a generic statement that
    also covers the case where none exists.
    """
    entries: list[SlotValue] = []
    for case in e.facts.cases:
        entries.append(SlotValue(field=GroundedField.CASE_NUMBER, value=case.case_number))
        entries.append(SlotValue(field=GroundedField.CASE_STATUS, value=case.status))
        entries.append(
            SlotValue(field=GroundedField.FILED_ON, value=format_date(case.filed_on, e.lang))
        )
    statement = (
        _DISPUTE_STATUS_OUTCOME_STATEMENT[e.lang]
        if e.facts.cases
        else _NO_CASE_FOUND_OUTCOME_STATEMENT[e.lang]
    )
    entries.append(SlotValue(field=GroundedField.OUTCOME_STATEMENT, value=statement))
    return tuple(entries)


def _policy_answer(e: RenderEnvelope) -> tuple[SlotValue, ...]:
    """The title of the policy section cited, and every figure the policy engine supplied."""
    entries = [SlotValue(field=GroundedField.SOURCE_TITLE, value=e.sources[0].title_for(e.lang))]
    entries.extend(
        SlotValue(field=GroundedField.POLICY_VALUE, value=value.value)
        for value in e.facts.policy_values
    )
    return tuple(entries)


def _handoff(e: RenderEnvelope) -> tuple[SlotValue, ...]:
    """The generic statement that a person must review, the ticket reference and the contact window
    when they are known.
    """
    entries = [
        SlotValue(field=GroundedField.OUTCOME_STATEMENT, value=_HANDOFF_OUTCOME_STATEMENT[e.lang])
    ]
    if e.facts.ticket_ref is not None:
        entries.append(SlotValue(field=GroundedField.TICKET_REF, value=e.facts.ticket_ref))
    if e.facts.contact_within_hours is not None:
        entries.append(
            SlotValue(
                field=GroundedField.CONTACT_WITHIN_HOURS, value=str(e.facts.contact_within_hours)
            )
        )
    return tuple(entries)


def _none(_e: RenderEnvelope) -> tuple[SlotValue, ...]:
    """No grounded values: an intent that states no facts has nothing to ground."""
    return ()


_Builder = Callable[[RenderEnvelope], tuple[SlotValue, ...]]

# Exhaustive over Intent (tested): an intent with no grounded fields (INTENT_ALLOWED_FIELDS[intent]
# is empty) still gets an entry here, returning no values, so this table is total.
_BUILDERS: dict[Intent, _Builder] = {
    Intent.CLARIFY: _none,
    Intent.PRESENT_TRANSACTIONS: _present_transactions,
    Intent.CONFIRM_FILING: _confirm_filing,
    Intent.FILING_RESULT: _filing_result,
    Intent.INELIGIBLE: _ineligible,
    Intent.DISPUTE_STATUS: _dispute_status,
    Intent.POLICY_ANSWER: _policy_answer,
    Intent.ABSTAIN: _none,
    Intent.REFUSE: _none,
    Intent.HANDOFF: _handoff,
    Intent.FAREWELL: _none,
}


def slot_values_for(envelope: RenderEnvelope) -> SlotValues:
    """The grounded values ``envelope`` makes available, keyed by field, in citation order."""
    return SlotValues(entries=_BUILDERS[envelope.intent](envelope))
