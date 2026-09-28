"""
Verification Contract, Service Version 1
=========================================

Overview
--------
The contract between the model-rendered path and the output verifier: the model's raw candidate
text, the grounded values the controller derived for the envelope, and the verifier's own
accept/reject result. The envelope contract's ``GroundedField`` is the shared vocabulary; this
module holds nothing the envelope contract itself needs.

Scope
-----
In: ``CandidateReply`` (what the model wrote), ``SlotValue``/``SlotValues`` (what the controller
grounded, keyed by field), ``RejectionReason`` and ``VerifierResult`` (what the verifier decided).
Out: running the verification algorithm (``app.conversation.verifier``), producing slot values
from an envelope (the dialogue controller, a later slice) and the model call itself (``app.llm``).

Design Principles
-----------------
- The model never sees a grounded value, only the field names it may reference: ``CandidateReply``
  carries no fact, decision or source, so nothing this module accepts as "what the model wrote"
  can already contain a grounded value to fabricate around.
- A field can be cited more than once (``DisputeFacts.cases`` holds up to three cases, each with
  its own case number and filing date): ``SlotValues`` holds a tuple of entries rather than one
  value per field, and successive placeholder occurrences for the same field consume its entries
  in order. A single-valued fact is simply one entry.
- ``RejectionReason`` names *why* a candidate was rejected, not just that it was, so a verifier
  test can assert the specific failure mode it seeded rather than only the outcome.
- The models are immutable and reject unknown fields, matching every other service contract.

Runtime Contract
----------------
``CandidateReply``, ``SlotValue``, ``SlotValues``, ``RejectionReason``, ``VerifierResult``.
"""

from __future__ import annotations

# Standard libraries
from enum import StrEnum  # Closed set of rejection reasons
from typing import Annotated, Literal  # Bounded fields, closed literals

# Third-party libraries
from pydantic import Field, model_validator  # Bounded fields, cross-field rules

# Local modules
from contracts.service_v1.envelope import (  # The shared contract base and vocabulary
    ContractModel,
    GroundedField,
    SafeText,
)

# -----------------------------------------------------------------------------
# What the model wrote
# -----------------------------------------------------------------------------


class CandidateReply(ContractModel):
    """The model's raw output for one envelope, before slot substitution.

    Carries no ``intent``: the envelope the verifier is called alongside already names it, and a
    self-reported intent on the candidate would be exactly as trustworthy as the free text around
    it — there is nothing independent to check it against.
    """

    raw_text: Annotated[SafeText, Field(min_length=1, max_length=2000)]


# -----------------------------------------------------------------------------
# What the controller grounded
# -----------------------------------------------------------------------------


class SlotValue(ContractModel):
    """One grounded value the model-rendered path may cite, already formatted per language.

    Built from the envelope's own ``facts``, ``decisions`` or ``sources`` only — the model never
    produces or sees this string, only ``field``'s name.
    """

    field: GroundedField
    value: Annotated[SafeText, Field(min_length=1, max_length=200)]


class SlotValues(ContractModel):
    """The grounded values derived from one envelope, in the order the reply may cite them.

    Two entries naming the same field are consumed in the order they appear here by successive
    placeholder occurrences for that field (see ``GroundedField``'s docstring) — the mechanism a
    reply that lists several cases needs, one entry per case.
    """

    entries: tuple[SlotValue, ...] = ()


# -----------------------------------------------------------------------------
# What the verifier decided
# -----------------------------------------------------------------------------


class RejectionReason(StrEnum):
    """Why a candidate reply was rejected."""

    DIGIT_OUTSIDE_SLOT = "digit_outside_slot"
    UNDECLARED_PLACEHOLDER = "undeclared_placeholder"
    UNRESOLVED_PLACEHOLDER = "unresolved_placeholder"
    REQUIRED_FIELD_MISSING = "required_field_missing"


class VerifierResult(ContractModel):
    """The verifier's decision for one candidate reply."""

    outcome: Literal["accepted", "rejected"]
    rendered_text: SafeText | None = None
    reasons: tuple[RejectionReason, ...] = ()

    @model_validator(mode="after")
    def _reasons_and_text_agree_with_the_outcome(self) -> VerifierResult:
        """Rejected names why and carries no text; accepted carries the text and names nothing."""
        if self.outcome == "rejected":
            if not self.reasons:
                raise ValueError("a rejected result names at least one reason")
            if self.rendered_text is not None:
                raise ValueError("a rejected result carries no rendered text")
        elif self.reasons:
            raise ValueError("an accepted result names no reason")
        elif self.rendered_text is None:
            raise ValueError("an accepted result carries the rendered text")
        return self
