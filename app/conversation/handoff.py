"""
Handoff Builder
===============

Overview
--------
Assembles the structured packet a person receives when a conversation is handed over
(``contracts.service_v1.handoff.HandoffPacket``). Pure: every fact the packet needs is a parameter,
never read from a store or the clock here, so the controller (a later change) decides what to pass
and this module cannot silently disagree with it about what happened.

Scope
-----
In: ``build_packet`` and ``mask_customer_id``, the one place a customer identifier is turned into
the four-asterisk label an agent may see.
Out: writing the packet (the outbox, a separate module), generating the ticket reference (the
outbox's job, since it is the one that knows the ticket is unique before it commits), deciding
which trigger applies to which situation (the controller).

Design Principles
-----------------
- No I/O, no clock, no randomness: every date, instant and identifier is a parameter, so a test
  builds a packet without a database or a fixed system clock.
- ``needs_language_routing`` is computed, never accepted as a parameter that could disagree with
  ``language`` — the contract's own validator would refuse that anyway, but computing it here means
  the disagreement can never be constructed in the first place.
- ``mask_customer_id`` keeps at most the last four characters of the identifier; a customer
  identifier is never a field a packet or a log line carries in full (PII minimization).

Runtime Contract
----------------
``build_packet(...) -> HandoffPacket``. ``mask_customer_id(customer_id) -> str``.
"""

from __future__ import annotations

# Standard libraries
import re  # Non-alphanumeric characters stripped before masking
from datetime import date  # The reference date the packet used

# Local modules
from app.domain.policy.models import DisputeCategory, ReasonCode  # Shared vocabulary
from contracts.service_v1.envelope import (  # Shared base, types and vocabulary
    Lang,
    RiskEvidence,
    SourceRef,
    TransactionFact,
    UtcDatetime,
)
from contracts.service_v1.handoff import (  # The packet this module builds
    ActionRecord,
    CustomerLabel,
    Evidence,
    HandoffPacket,
    HandoffTrigger,
    OpenQuestion,
)

_MASK_PREFIX = "****"
_MASK_VISIBLE_CHARS = 4
_MASK_MIN_VISIBLE_CHARS = 2


def mask_customer_id(customer_id: str) -> str:
    """``customer_id`` as an agent may see it: four asterisks, then up to its last four characters.

    Only alphanumeric characters are kept, matching ``CustomerLabel.masked_id``'s own pattern; a
    hyphen or another separator in the source identifier is dropped rather than masked. The
    session identifier pattern (``app.security.sessions``) allows an identifier with fewer than
    two alphanumeric characters (for example ``"_-"``); padding with ``"0"`` on the left — never a
    real digit of the identifier — guarantees the two-to-four-character result the contract
    requires holds for every identifier the session layer accepts, not only the common ones a
    handful of examples happen to cover.
    """
    alnum = re.sub(r"[^A-Za-z0-9]", "", customer_id)
    visible = alnum[-_MASK_VISIBLE_CHARS:].rjust(_MASK_MIN_VISIBLE_CHARS, "0")
    return _MASK_PREFIX + visible


def build_packet(
    *,
    ticket_ref: str,
    reference_date: date,
    created_at: UtcDatetime,
    language: Lang,
    trigger: HandoffTrigger,
    first_name: str,
    customer_id: str,
    request_summary: str,
    reason_codes: tuple[ReasonCode, ...],
    policy_version: str,
    category: DisputeCategory | None = None,
    verified_facts: tuple[TransactionFact, ...] = (),
    actions: tuple[ActionRecord, ...] = (),
    attempted_action: ActionRecord | None = None,
    existing_case_number: str | None = None,
    sources: tuple[SourceRef, ...] = (),
    risk: RiskEvidence | None = None,
    open_questions: tuple[OpenQuestion, ...] = (),
) -> HandoffPacket:
    """The packet for one handoff; every part beyond the required ones may be empty."""
    return HandoffPacket(
        ticket_ref=ticket_ref,
        reference_date=reference_date,
        created_at=created_at,
        language=language,
        needs_language_routing=language != "es",
        trigger=trigger,
        customer=CustomerLabel(first_name=first_name, masked_id=mask_customer_id(customer_id)),
        category=category,
        request_summary=request_summary,
        verified_facts=verified_facts,
        actions=actions,
        attempted_action=attempted_action,
        existing_case_number=existing_case_number,
        evidence=Evidence(
            reason_codes=reason_codes, policy_version=policy_version, sources=sources, risk=risk
        ),
        open_questions=open_questions,
    )
