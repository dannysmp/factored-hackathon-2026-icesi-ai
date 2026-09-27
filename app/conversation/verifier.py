"""
Output Verifier
===============

Overview
--------
Checks a model-rendered candidate reply against its envelope and substitutes the grounded values
it names, or rejects it — the mechanism D76 assigned this slice: the model never writes a digit
itself, the renderer (here, the substitution step) fills declared placeholders from grounded
values only, and any digit the model wrote on its own is rejected before it ever reaches a
customer. A template-mode reply never reaches this module: it is already fully grounded by
construction (``app.conversation.renderer``), and a refusal cannot be model-rendered at all
(``contracts.service_v1.envelope``'s own validator).

Scope
-----
In: ``verify()`` — the one function this module exports, and the private substitution/scan it runs.
Out: producing ``CandidateReply`` (the LLM adapter) and ``SlotValues`` (the dialogue controller, a
later slice, from the same envelope this call receives) are both someone else's job; this module
only checks what it is handed against what the envelope allows.

Design Principles
-----------------
- Structural rejection, not detection: any numeral character anywhere in the model's raw text
  rejects the reply outright, before any placeholder is even parsed — checked with
  ``str.isnumeric()``, not ``str.isdigit()``, so a vulgar fraction, a circled digit or a
  single-character Roman numeral (``½``, ``⑩``, ``Ⅻ``) rejects exactly like an ASCII digit, not
  only the narrower set ``isdigit()`` recognizes. This is the property D76 asked for after the
  prior text-based numbers guard was found gameable — nothing here inspects the *finished*
  sentence for a suspicious-looking number, because there is no numeral character left in it to
  inspect by the time substitution runs.
- Cross-customer leak scan and decision consistency fall out of the same mechanism rather than a
  second detector: ``SlotValues`` is built from this envelope's own facts, decisions and sources
  (a caller's job, not verified here), so a value from another customer or a contradicted decision
  has nowhere to enter — the only way a digit or a decision-bearing statement reaches the reply is
  a placeholder resolved from this envelope's own grounded entries.
- Placeholders are ``{{field}}``, lowercase and underscored, one of ``GroundedField``'s members; any
  other brace-delimited text is malformed and rejects the reply the same as an undeclared field.
- Multiple entries for the same field are consumed in the order ``SlotValues`` lists them by
  successive occurrences of that field's placeholder, so a reply that lists several cases pairs
  each placeholder with the next case in order.

Runtime Contract
----------------
``verify(envelope, candidate, slot_values) -> VerifierResult``.

Limitations
-----------
The rejection scans for a *character* Unicode itself classifies as numeric; a quantity spelled
entirely in ordinary letters (a Roman numeral spelled with plain Latin letters, or a number spelled
out in words, in any language) contains no such character and is not caught. Closing that would
mean checking the *meaning* of the text rather than its character classes, which is a different,
open-ended problem this slice does not attempt — the same class of gap already disclosed for a
non-numeric fact stated without going through a declared field at all.
"""

from __future__ import annotations

# Standard libraries
import re  # Placeholder scan

# Local modules
from contracts.service_v1.envelope import (  # The intent's allowed/required fields
    INTENT_ALLOWED_FIELDS,
    INTENT_REQUIRED_FIELDS,
    GroundedField,
    RenderEnvelope,
)
from contracts.service_v1.verification import (  # The candidate, its slots and the verdict
    CandidateReply,
    RejectionReason,
    SlotValues,
    VerifierResult,
)

_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")


def _malformed_braces(text: str, matches: list[re.Match[str]]) -> bool:
    """A ``{`` or ``}`` outside every well-formed placeholder match: malformed syntax."""
    covered = {index for match in matches for index in range(match.start(), match.end())}
    return any(char in "{}" for index, char in enumerate(text) if index not in covered)


def verify(
    envelope: RenderEnvelope, candidate: CandidateReply, slot_values: SlotValues
) -> VerifierResult:
    """Check ``candidate`` against ``envelope`` and substitute its declared placeholders.

    Parameters
    ----------
    envelope : RenderEnvelope
        The envelope the candidate was written for; its ``intent`` bounds which fields the reply
        may name, and its ``decisions``/``facts``/``sources`` are never read directly here — they
        reach the reply only through ``slot_values``, built from them by the caller.
    candidate : CandidateReply
        The model's raw text, before substitution.
    slot_values : SlotValues
        The grounded values this envelope makes available, keyed by field, in citation order.

    Returns
    -------
    VerifierResult
        ``accepted`` with the substituted text, or ``rejected`` naming every reason that applies.
    """
    text = candidate.raw_text
    reasons: set[RejectionReason] = set()

    if any(char.isnumeric() for char in text):
        reasons.add(RejectionReason.DIGIT_OUTSIDE_SLOT)

    matches = list(_PLACEHOLDER.finditer(text))
    if _malformed_braces(text, matches):
        reasons.add(RejectionReason.UNDECLARED_PLACEHOLDER)

    allowed = INTENT_ALLOWED_FIELDS[envelope.intent]
    queues: dict[GroundedField, list[str]] = {}
    for entry in slot_values.entries:
        queues.setdefault(entry.field, []).append(entry.value)

    used: set[GroundedField] = set()
    pieces: list[str] = []
    cursor = 0
    for match in matches:
        pieces.append(text[cursor : match.start()])
        cursor = match.end()
        try:
            field = GroundedField(match.group(1))
        except ValueError:
            field = None
        if field is None or field not in allowed:
            reasons.add(RejectionReason.UNDECLARED_PLACEHOLDER)
            continue
        queue = queues.get(field)
        if not queue:
            reasons.add(RejectionReason.UNRESOLVED_PLACEHOLDER)
            continue
        pieces.append(queue.pop(0))
        used.add(field)
    pieces.append(text[cursor:])

    required = INTENT_REQUIRED_FIELDS[envelope.intent]
    if not required <= used:
        reasons.add(RejectionReason.REQUIRED_FIELD_MISSING)

    if reasons:
        ordered = tuple(reason for reason in RejectionReason if reason in reasons)
        return VerifierResult(outcome="rejected", reasons=ordered)
    return VerifierResult(outcome="accepted", rendered_text="".join(pieces))
