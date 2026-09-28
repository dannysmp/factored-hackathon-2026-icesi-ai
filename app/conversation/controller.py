"""
Dialogue Controller
====================

Overview
--------
Turns one customer message into a reply: it reads the conversation's state, asks the understanding
port what the message means, decides deterministically what happens next (never the model), calls
the scoped tools it needs, and renders the fixed-wording reply. This is the orchestration the
architecture calls out explicitly: the model understands and renders language; this module decides
and acts.

Scope
-----
In: ``DialogueController.handle_turn(request, *, principal) -> TurnResponse`` and everything it
takes to get there — routing by intent, the missing-slot guard, transaction search and matching,
policy evaluation, filing and its read-back verification, and escalation to a person.
Out: understanding a message (``app.conversation.understanding``), storing state
(``app.conversation.store``/``app.persistence.dialogue_store``), the tools themselves
(``app.tools``, ``app.persistence.reads``), rendering fixed wording
(``app.conversation.renderer``), the turns endpoint and its body-size cap (a later change in this
same slice).

Design Principles
-----------------
- **Built fresh per request.** Every collaborator is injected at construction; there is no module-
  level state and no singleton, so nothing about one customer's turn can leak into another's.
- **One route per intent, exhaustively.** ``_ROUTES`` covers every ``NluIntent``, mirroring
  ``TEMPLATE_INTENTS``'s own completeness idiom: an intent added to the contract without a route
  here fails the tests, not silently falls through.
- **The guard decides what's missing; this module decides what to do about it.**
  ``app.conversation.guard.required_slot`` is the single source of truth for "is a transaction or a
  reason still missing"; identifying which transaction a given hint refers to (the search) is this
  module's own job, run whenever a hint is available and none is selected yet — independent of
  what the guard says about the *other* slot, since a hint is only ever available during the turn
  it was given (no raw text or hint is carried in ``DialogueState``, AC-E5-57).
- **Verify before report.** A filed case is always read back through ``get_case`` before the
  customer is told it succeeded; a mismatch or a failed read is a ``FILING_UNVERIFIED`` handoff,
  never a claimed success.
- **The create tool is reached through one door.** Filing calls
  ``app.tools.create_dispatch.create_dispute_case``, never the port directly and never the general
  dispatcher (which excludes it by design), matching the structural test that enforces this.
- **Every side effect this module performs is idempotent**, so replaying a turn — whether because
  the client retried or because a concurrent request already won — never double-files a case or
  double-registers a handoff: ``create_dispute_case``'s ``idempotency_key`` is derived
  deterministically from the turn id, and ``HandoffOutbox.record`` is already idempotent by
  ``(session_id, turn_id)``.
- **A duplicate turn is answered without redoing anything unsafe.** The common case — the exact
  same turn id already recorded on the loaded state — is answered directly from that state: a
  fresh read-back for a filed case, a direct render for a handoff, a pure re-render for a pending
  clarification. Every other outcome (ineligible, cancelled, farewell, a plain informational
  reply) has no tool call that isn't already safe to repeat, so it is simply recomputed.
- **PII minimization.** No raw customer text reaches a store, a log or a handoff packet; a handoff
  names its category and reason codes, never a transcript.

Runtime Contract
----------------
``DialogueController(understanding, store, tool_port, retriever, policy, outbox, *, domain_date,
now)`` with ``handle_turn(request: TurnRequest, *, principal: Principal) -> TurnResponse``.
``HandoffOutbox`` (protocol): the port this module writes a handoff through.

Limitations
-----------
A single-match search result is accepted immediately (``PRESENT_ONE`` is informational, not a
second confirmation gate) and two or more matches ask for more detail rather than presenting a
numbered list — the same v1 scope decision already made for slot collection, since neither a
pending-candidate field nor a multi-candidate list exists in ``DialogueState`` yet. A session
identifies and evaluates at most one transaction/category pair: nothing here resets
``selected_ref``/``category`` once set, so a second, different dispute needs a new session. The
handoff packet's ``first_name`` is a placeholder: no tool exposes the customer's first name yet.
A duplicate turn's handoff replay always uses the generic reviewing wording, which may differ from
the original trigger-specific wording (fraud, card loss, a person requested) though it states the
same outcome and ticket. Contact-within-hours and structured risk evidence are not populated in a
handoff packet: neither is available from the tools this module calls.
"""

from __future__ import annotations

# Standard libraries
import hashlib  # Deterministic idempotency key derived from the turn id
import logging  # Progress events, never print
from collections.abc import Callable  # Type of one route's handler
from datetime import date  # Domain date the controller was built with
from typing import Protocol  # The handoff outbox port this module depends on

# Third-party libraries
import psycopg  # Distinguishing an outbox write failure from every other outcome

# Local modules
from app.conversation.facts import to_envelope_case, to_envelope_transaction
from app.conversation.guard import required_slot
from app.conversation.policy_answer import answer as policy_answer
from app.conversation.renderer import RenderedReply, demo_notice, render
from app.conversation.state import ConversationPhase, DialogueState
from app.conversation.store import Conflict, DialogueStore, DuplicateTurn
from app.conversation.understanding import Understanding
from app.domain.policy.models import DisputeCategory, Outcome, Policy, PolicyDecision, ReasonCode
from app.persistence.handoff_outbox import HandoffContent
from app.retrieval.lexical import LexicalRetriever
from app.security.errors import ErrorCode, ProblemError
from app.security.sessions import Clock, Principal
from app.tools.create_dispatch import create_dispute_case
from app.tools.dispatcher import dispatch
from contracts.service_v1 import tools as tool_contracts
from contracts.service_v1.api import TurnRequest, TurnResponse
from contracts.service_v1.envelope import (
    CUSTOMER_REASON_OF,
    CustomerReason,
    Decision,
    DisputeFacts,
    Intent,
    Lang,
    RenderEnvelope,
    Slot,
    SourceRef,
    TemplateId,
)
from contracts.service_v1.envelope import TransactionFact as EnvelopeTransactionFact
from contracts.service_v1.handoff import ActionRecord, HandoffPacket, HandoffTrigger
from contracts.service_v1.nlu import ConfirmationAnswer, NluIntent, NluResult, TransactionHint
from contracts.service_v1.tools import (
    CreateDisputeCaseRequest,
    EvaluateDisputeRequest,
    ToolFailure,
    ToolPort,
    ToolRefusalCode,
    TransactionFilters,
)

logger = logging.getLogger(__name__)

# A placeholder until a tool exposes the customer's first name (see Limitations): agent-facing
# only, never shown to the customer.
_UNKNOWN_FIRST_NAME = "Customer"

_ROUTED_HANDOFFS = frozenset({TemplateId.HANDOFF_REVIEW, TemplateId.HANDOFF_FRAUD})

_EMPTY_FACTS = DisputeFacts()

_ASK_TEMPLATE_OF: dict[Slot, TemplateId] = {
    Slot.TRANSACTION: TemplateId.CLARIFY_TRANSACTION,
    Slot.REASON: TemplateId.CLARIFY_REASON,
    Slot.CONFIRMATION: TemplateId.CLARIFY_CONFIRMATION,
}

# Every reason code the policy engine can escalate with, and why a person is being asked to look
# at it. Exhaustive over the escalate reasons (tested): a reason added to that closed set without
# an entry here fails the tests, so an escalation is never silently unrouted.
_ESCALATE_TRIGGER_OF: dict[ReasonCode, HandoffTrigger] = {
    ReasonCode.ESCALATE_FRAUD_CLAIM: HandoffTrigger.FRAUD_REPORT,
    ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE: HandoffTrigger.LOW_UNDERSTANDING,
    ReasonCode.ESCALATE_REPEAT_COMPLAINER: HandoffTrigger.REPEAT_COMPLAINER,
    ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD: HandoffTrigger.AMOUNT_REVIEW,
    ReasonCode.ESCALATE_AMOUNT_UNKNOWN: HandoffTrigger.AMOUNT_UNKNOWN,
    ReasonCode.ESCALATE_RISK_SCORE: HandoffTrigger.RISK_SCORE,
}

# The agent-facing summary of what brought the conversation to a person, in the system's own
# words — never the customer's raw text (PII minimization, AC-E5-57). Exhaustive over
# ``HandoffTrigger`` (tested).
_REQUEST_SUMMARY_OF: dict[HandoffTrigger, str] = {
    HandoffTrigger.FRAUD_REPORT: "Customer reported a possible fraud.",
    HandoffTrigger.CARD_LOSS: "Customer reported a lost or stolen card.",
    HandoffTrigger.CUSTOMER_REQUEST: "Customer asked to speak with a person.",
    HandoffTrigger.AMOUNT_REVIEW: "A dispute filing requires review of the transaction amount.",
    HandoffTrigger.REPEAT_COMPLAINER: "A dispute filing flagged the customer as a repeat filer.",
    HandoffTrigger.RISK_SCORE: "A dispute filing was flagged by the risk model.",
    HandoffTrigger.AMOUNT_UNKNOWN: "A dispute filing could not confirm the transaction amount.",
    HandoffTrigger.LOW_UNDERSTANDING: "The conversation could not identify what is needed.",
    HandoffTrigger.TOOL_FAILURE: "A system tool was unavailable while handling the request.",
    HandoffTrigger.FILING_UNVERIFIED: "A dispute filing could not be confirmed after creation.",
}


class HandoffOutbox(Protocol):
    """Where a handoff is written; the port ``PostgresHandoffOutbox`` implements."""

    def record(
        self, content: HandoffContent, *, session_id: str, turn_id: str, trace_id: str
    ) -> HandoffPacket:
        """Write the outbox row for ``content`` and return its packet.

        Raises
        ------
        psycopg.Error
            The store could not be reached; the caller renders ``HANDOFF_NOT_REGISTERED`` instead
            of claiming the request was registered.
        """
        ...


def _idempotency_key(turn_id: str) -> str:
    """A deterministic key for ``create_dispute_case``, derived from the turn id.

    ``turn_id`` may be longer than the idempotency key's own 32-character bound, so it is hashed
    rather than passed through; the same turn always yields the same key, and a retried turn's
    filing call replays through the tool's own idempotency mechanism instead of filing twice.
    """
    return hashlib.sha256(turn_id.encode("utf-8")).hexdigest()[:32]


def _matches_hint(fact: tool_contracts.TransactionFact, hint: TransactionHint) -> bool:
    """Whether ``fact`` could be what the customer described in ``hint``.

    Every part of ``hint`` that was given must agree; a part the source data cannot answer (an
    absent merchant and description, an unknown amount) never matches a hint that names it.
    """
    if hint.merchant is not None:
        label = fact.merchant or fact.description
        if label is None or hint.merchant.lower() not in label.lower():
            return False
    money = fact.amount.money
    if hint.amount is not None and (money is None or money.amount != hint.amount):
        return False
    if hint.currency is not None and (money is None or money.currency != hint.currency):
        return False
    return hint.product_last4 is None or fact.product.last4 == hint.product_last4


def _escalate_decision(policy: Policy) -> Decision:
    """The customer-facing decision every routed handoff carries: needs a person, nothing more."""
    return Decision(
        outcome=Outcome.ESCALATE,
        customer_reason=CustomerReason.NEEDS_REVIEW,
        policy_version=policy.version,
        requires_confirmation=False,
    )


class DialogueController:
    """Decides and acts on one customer turn; built fresh for every request."""

    def __init__(
        self,
        understanding: Understanding,
        *,
        store: DialogueStore,
        tool_port: ToolPort,
        retriever: LexicalRetriever,
        policy: Policy,
        outbox: HandoffOutbox,
        domain_date: date,
        now: Clock,
    ) -> None:
        self._understanding = understanding
        self._store = store
        self._tool_port = tool_port
        self._retriever = retriever
        self._policy = policy
        self._outbox = outbox
        self._domain_date = domain_date
        self._now = now
        # Set once per call, at the top of handle_turn: every private helper below reads the
        # current turn's own request and principal from here rather than threading them through
        # every method signature. Safe because one instance ever handles exactly one turn.
        self._request: TurnRequest | None = None
        self._principal: Principal | None = None

    # -------------------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------------------

    def handle_turn(self, request: TurnRequest, *, principal: Principal) -> TurnResponse:
        """Process one customer turn and return the reply.

        Raises
        ------
        ProblemError
            ``turn_conflict`` (409) when a different turn already advanced this session past the
            version this call read; the client should refetch and retry.
        """
        self._request = request
        self._principal = principal
        session_id = principal.session_id
        current = self._store.get(session_id)

        if current is not None and current.last_turn_id == request.turn_id:
            return self._respond(current, self._replay_envelope(current))

        state, expected_version, result = self._start_turn(current, request)
        new_state, envelope = self._advance(state, result)

        try:
            saved = self._store.save(
                new_state,
                expected_version=expected_version,
                turn_id=request.turn_id,
                now=self._now(),
            )
        except DuplicateTurn as duplicate:
            return self._respond(duplicate.state, self._replay_envelope(duplicate.state))
        except Conflict as conflict:
            raise ProblemError(
                ErrorCode.TURN_CONFLICT,
                409,
                "The conversation moved on",
                "Fetch the current state and try again.",
            ) from conflict

        return self._respond(saved, envelope)

    def _start_turn(
        self, current: DialogueState | None, request: TurnRequest
    ) -> tuple[DialogueState, int, NluResult]:
        """The state to advance from, the version it was read at, and this message's understanding.

        A brand-new session starts at expected version 0 (a fresh insert, unconditional on it —
        ``DialogueStore.save``'s own documented behavior); its language is the first message's own,
        or Spanish when the message is too ambiguous to tell (AC: es and pt are both required).
        """
        if current is not None:
            result = self._understanding.understand(request.text, language_hint=current.lang)
            return current, current.version, result

        result = self._understanding.understand(request.text, language_hint=None)
        lang: Lang = result.language if result.language is not None else "es"
        fresh = DialogueState(
            session_id=self._session_id(),
            lang=lang,
            phase=ConversationPhase.STARTED,
            updated_at=self._now(),
        )
        return fresh, 0, result

    def _session_id(self) -> str:
        assert self._principal is not None  # noqa: S101 - set at the top of handle_turn
        return self._principal.session_id

    # -------------------------------------------------------------------------------------
    # Turn advancement
    # -------------------------------------------------------------------------------------

    def _advance(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Decide and act on ``result`` against ``state``; returns the state to save and the reply.

        A brand-new session whose first message is too ambiguous to place in Spanish or
        Portuguese is offered both, deferring its actual request to the next turn (the renderer's
        own documented job of combining a best guess with the offer to switch, in one reply).
        """
        if state.last_turn_id is None and result.language is None:
            return state, self._envelope(state, Intent.CLARIFY, TemplateId.LANGUAGE_OFFER)
        handler = _ROUTES[result.intent]
        return handler(self, state, result)

    # -------------------------------------------------------------------------------------
    # Per-intent handlers
    # -------------------------------------------------------------------------------------

    def _handle_file_dispute(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        if result.category is not None and state.category is None:
            state = state.model_copy(update={"category": result.category})

        slot = required_slot(result, state)
        if slot is Slot.TRANSACTION:
            return self._ask(state, Slot.TRANSACTION)
        if state.selected_ref is None:
            return self._resolve_transaction(state, result.transaction)
        if slot is Slot.REASON:
            return self._ask(state, Slot.REASON)

        assert state.category is not None  # noqa: S101 - guaranteed by required_slot above
        return self._evaluate_and_present(state, state.selected_ref, state.category)

    def _handle_list_transactions(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        page = dispatch(
            self._tool_port, tool_contracts.Tool.LIST_TRANSACTIONS, TransactionFilters()
        )
        if isinstance(page, ToolFailure):
            return self._tool_failure_handoff(state, tool=tool_contracts.Tool.LIST_TRANSACTIONS)
        if not page.items:
            return state, self._envelope(state, Intent.CLARIFY, TemplateId.NOT_FOUND)
        facts = DisputeFacts(
            transactions=tuple(to_envelope_transaction(item) for item in page.items),
            candidate_count=page.total_count,
        )
        return state, self._envelope(
            state, Intent.PRESENT_TRANSACTIONS, TemplateId.PRESENT_LIST, facts=facts
        )

    def _handle_dispute_status(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        cases = dispatch(self._tool_port, tool_contracts.Tool.LIST_DISPUTE_CASES)
        if isinstance(cases, ToolFailure):
            return self._tool_failure_handoff(state, tool=tool_contracts.Tool.LIST_DISPUTE_CASES)
        if not cases:
            return state, self._envelope(state, Intent.DISPUTE_STATUS, TemplateId.NO_CASE_FOUND)
        facts = DisputeFacts(cases=tuple(to_envelope_case(case) for case in cases[:3]))
        return state, self._envelope(
            state, Intent.DISPUTE_STATUS, TemplateId.DISPUTE_STATUS, facts=facts
        )

    def _handle_policy_question(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        query = result.policy_query or ""
        answered = policy_answer(query, state.lang, state.category, self._retriever, self._policy)
        if answered.source is None:
            return state, self._envelope(state, Intent.ABSTAIN, TemplateId.ABSTAIN_POLICY)
        facts = DisputeFacts(policy_values=answered.values)
        return state, self._envelope(
            state,
            Intent.POLICY_ANSWER,
            TemplateId.POLICY_ANSWER,
            facts=facts,
            sources=(answered.source,),
        )

    def _handle_confirmation(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        if state.pending_slot is not Slot.CONFIRMATION:
            return self._fallback(state, result)

        answer = result.confirmation
        if answer is ConfirmationAnswer.NO:
            new_state = state.with_slot_filled().with_phase(ConversationPhase.CLOSED)
            return new_state, self._envelope(new_state, Intent.CLARIFY, TemplateId.FILING_CANCELLED)
        if answer is not ConfirmationAnswer.YES:
            return self._ask(state, Slot.CONFIRMATION)

        assert state.selected_ref is not None and state.category is not None  # noqa: S101
        decision = dispatch(
            self._tool_port,
            tool_contracts.Tool.EVALUATE_DISPUTE,
            EvaluateDisputeRequest(transaction_ref=state.selected_ref, category=state.category),
        )
        if isinstance(decision, ToolFailure):
            return self._tool_failure_handoff(state, tool=tool_contracts.Tool.EVALUATE_DISPUTE)
        if decision.outcome is not Outcome.ELIGIBLE:
            return self._present_non_eligible(state, state.category, decision)
        return self._file_and_verify(state, state.selected_ref, state.category, decision)

    def _handle_report_fraud(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        return self._handoff(
            state,
            trigger=HandoffTrigger.FRAUD_REPORT,
            reason_codes=(ReasonCode.ESCALATE_FRAUD_CLAIM,),
            template=TemplateId.HANDOFF_FRAUD,
        )

    def _handle_report_card_loss(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        return self._handoff(
            state,
            trigger=HandoffTrigger.CARD_LOSS,
            reason_codes=(),
            template=TemplateId.HANDOFF_CARD_LOSS,
        )

    def _handle_request_person(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        return self._handoff(
            state,
            trigger=HandoffTrigger.CUSTOMER_REQUEST,
            reason_codes=(),
            template=TemplateId.HANDOFF_REQUESTED,
        )

    def _handle_request_reversal(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        return state, self._envelope(state, Intent.REFUSE, TemplateId.REFUSE_REVERSAL)

    def _handle_unsupported_action(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        return state, self._envelope(state, Intent.REFUSE, TemplateId.REFUSE_UNSUPPORTED)

    def _handle_switch_language(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        assert result.requested_language is not None  # noqa: S101 - guaranteed by the contract
        new_state = state.with_language(result.requested_language)
        return new_state, self._envelope(new_state, Intent.CLARIFY, TemplateId.GREETING)

    def _handle_small_talk(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        return state, self._envelope(state, Intent.CLARIFY, TemplateId.GREETING)

    def _handle_farewell(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        new_state = state.with_phase(ConversationPhase.CLOSED)
        return new_state, self._envelope(
            new_state, Intent.FAREWELL, TemplateId.FAREWELL, end_session=True
        )

    def _handle_unroutable(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """``choice`` and ``correction`` share ``unclear``'s fallback (see Limitations)."""
        return self._fallback(state, result)

    # -------------------------------------------------------------------------------------
    # Shared decision logic
    # -------------------------------------------------------------------------------------

    def _fallback(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """No specific intent applies: re-ask whatever is pending, or offer the menu again."""
        if state.pending_slot is not None:
            return self._ask(state, state.pending_slot)
        return state, self._envelope(state, Intent.CLARIFY, TemplateId.GREETING)

    def _ask(self, state: DialogueState, slot: Slot) -> tuple[DialogueState, RenderEnvelope]:
        """Ask again for ``slot``, or escalate once the clarification budget is spent."""
        new_state = state.with_clarification(slot)
        if new_state.clarification_attempts >= self._policy.routing.clarification_budget:
            return self._handoff(
                new_state,
                trigger=HandoffTrigger.LOW_UNDERSTANDING,
                reason_codes=(ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,),
                template=TemplateId.HANDOFF_REVIEW,
            )
        return new_state, self._envelope(new_state, Intent.CLARIFY, _ASK_TEMPLATE_OF[slot])

    def _resolve_transaction(
        self, state: DialogueState, hint: TransactionHint
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Search for the transaction ``hint`` describes; its outcome is this turn's whole reply."""
        filters = (
            TransactionFilters(since=hint.date_on, until=hint.date_on)
            if hint.date_on is not None
            else TransactionFilters()
        )
        page = dispatch(self._tool_port, tool_contracts.Tool.LIST_TRANSACTIONS, filters)
        if isinstance(page, ToolFailure):
            return self._tool_failure_handoff(state, tool=tool_contracts.Tool.LIST_TRANSACTIONS)

        matches = tuple(item for item in page.items if _matches_hint(item, hint))
        if not matches:
            return state, self._envelope(state, Intent.CLARIFY, TemplateId.NOT_FOUND)
        if len(matches) > 1:
            new_state = state.with_clarification(Slot.TRANSACTION)
            if new_state.clarification_attempts >= self._policy.routing.clarification_budget:
                return self._handoff(
                    new_state,
                    trigger=HandoffTrigger.LOW_UNDERSTANDING,
                    reason_codes=(ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,),
                    template=TemplateId.HANDOFF_REVIEW,
                )
            return new_state, self._envelope(new_state, Intent.CLARIFY, TemplateId.PRESENT_NARROW)

        fact = to_envelope_transaction(matches[0])
        new_state = state.with_slot_filled().model_copy(update={"selected_ref": fact.ref})
        facts = DisputeFacts(transactions=(fact,), candidate_count=1, selected_ref=fact.ref)
        return new_state, self._envelope(
            new_state, Intent.PRESENT_TRANSACTIONS, TemplateId.PRESENT_ONE, facts=facts
        )

    def _evaluate_and_present(
        self, state: DialogueState, ref: str, category: DisputeCategory
    ) -> tuple[DialogueState, RenderEnvelope]:
        decision = dispatch(
            self._tool_port,
            tool_contracts.Tool.EVALUATE_DISPUTE,
            EvaluateDisputeRequest(transaction_ref=ref, category=category),
        )
        if isinstance(decision, ToolFailure):
            return self._tool_failure_handoff(state, tool=tool_contracts.Tool.EVALUATE_DISPUTE)
        if decision.outcome is not Outcome.ELIGIBLE:
            return self._present_non_eligible(state, category, decision)
        if not decision.requires_confirmation:
            return self._file_and_verify(state, ref, category, decision)

        transaction = dispatch(self._tool_port, tool_contracts.Tool.GET_TRANSACTION, ref)
        if isinstance(transaction, ToolFailure) or transaction is None:
            return self._tool_failure_handoff(state, tool=tool_contracts.Tool.GET_TRANSACTION)
        fact = to_envelope_transaction(transaction)

        new_state = state.model_copy(
            update={
                "phase": ConversationPhase.CONFIRMING,
                "pending_slot": Slot.CONFIRMATION,
                "clarification_attempts": 0,
            }
        )
        facts = DisputeFacts(
            transactions=(fact,), candidate_count=1, selected_ref=ref, category=category
        )
        decisions = (
            Decision(
                outcome=Outcome.ELIGIBLE,
                customer_reason=CustomerReason.ELIGIBLE,
                policy_version=decision.policy_version,
                requires_confirmation=True,
            ),
        )
        return new_state, self._envelope(
            new_state,
            Intent.CONFIRM_FILING,
            TemplateId.CONFIRM_FILING,
            facts=facts,
            decisions=decisions,
        )

    def _present_non_eligible(
        self, state: DialogueState, category: DisputeCategory, decision: PolicyDecision
    ) -> tuple[DialogueState, RenderEnvelope]:
        if decision.outcome is Outcome.INELIGIBLE:
            new_state = state.with_slot_filled().with_phase(ConversationPhase.CLOSED)
            decisions = (
                Decision(
                    outcome=Outcome.INELIGIBLE,
                    customer_reason=CUSTOMER_REASON_OF[decision.reason_code],
                    policy_version=decision.policy_version,
                ),
            )
            return new_state, self._envelope(
                new_state, Intent.INELIGIBLE, TemplateId.INELIGIBLE, decisions=decisions
            )

        trigger = _ESCALATE_TRIGGER_OF[decision.reason_code]
        template = (
            TemplateId.HANDOFF_FRAUD
            if decision.reason_code is ReasonCode.ESCALATE_FRAUD_CLAIM
            else TemplateId.HANDOFF_REVIEW
        )
        return self._handoff(
            state,
            trigger=trigger,
            reason_codes=(decision.reason_code, *decision.triggers),
            template=template,
            category=category,
            actions=(ActionRecord(action="evaluate_dispute", result=decision.reason_code.value),),
        )

    def _file_and_verify(
        self, state: DialogueState, ref: str, category: DisputeCategory, decision: PolicyDecision
    ) -> tuple[DialogueState, RenderEnvelope]:
        request = self._request
        assert request is not None  # noqa: S101 - set at the top of handle_turn
        idempotency_key = _idempotency_key(request.turn_id)
        result = create_dispute_case(
            self._tool_port,
            CreateDisputeCaseRequest(
                transaction_ref=ref,
                category=category,
                confirmed=True,
                idempotency_key=idempotency_key,
                decision=decision,
            ),
        )
        if isinstance(result, ToolFailure):
            return self._tool_failure_handoff(
                state, tool=tool_contracts.Tool.CREATE_DISPUTE_CASE, category=category
            )
        if not result.created:
            return self._present_creation_refusal(
                state, category, result.refusal, result.existing_case_number
            )

        assert result.case_number is not None  # noqa: S101 - guaranteed when created is True
        case = dispatch(self._tool_port, tool_contracts.Tool.GET_CASE, result.case_number)
        if (
            isinstance(case, ToolFailure)
            or case is None
            or case.transaction_ref != ref
            or case.category != category
        ):
            action = ActionRecord(action="create_dispute_case", result="unverified")
            return self._handoff(
                state,
                trigger=HandoffTrigger.FILING_UNVERIFIED,
                reason_codes=(),
                template=TemplateId.FILING_UNVERIFIED,
                category=category,
                existing_case_number=result.case_number,
                actions=(action,),
            )

        new_state = state.with_case_filed(result.case_number)
        case_fact = to_envelope_case(case)
        facts = DisputeFacts(
            cases=(case_fact,), expected_response_on=case_fact.expected_response_on
        )
        return new_state, self._envelope(
            new_state, Intent.FILING_RESULT, TemplateId.FILING_RESULT, facts=facts
        )

    def _present_creation_refusal(
        self,
        state: DialogueState,
        category: DisputeCategory,
        refusal: ToolRefusalCode | None,
        existing_case_number: str | None,
    ) -> tuple[DialogueState, RenderEnvelope]:
        if refusal is ToolRefusalCode.DUPLICATE_OPEN_CASE:
            new_state = state.with_slot_filled().with_phase(ConversationPhase.CLOSED)
            decisions = (
                Decision(
                    outcome=Outcome.INELIGIBLE,
                    customer_reason=CustomerReason.DUPLICATE_CASE,
                    policy_version=self._policy.version,
                ),
            )
            return new_state, self._envelope(
                new_state, Intent.INELIGIBLE, TemplateId.INELIGIBLE, decisions=decisions
            )
        action = ActionRecord(
            action="create_dispute_case", result=refusal.value if refusal is not None else "refused"
        )
        return self._handoff(
            state,
            trigger=HandoffTrigger.TOOL_FAILURE,
            reason_codes=(),
            template=TemplateId.HANDOFF_REVIEW,
            category=category,
            existing_case_number=existing_case_number,
            actions=(action,),
        )

    def _tool_failure_handoff(
        self,
        state: DialogueState,
        *,
        tool: tool_contracts.Tool,
        category: DisputeCategory | None = None,
    ) -> tuple[DialogueState, RenderEnvelope]:
        action = ActionRecord(action=tool.value, result="tool_failure")
        return self._handoff(
            state,
            trigger=HandoffTrigger.TOOL_FAILURE,
            reason_codes=(),
            template=TemplateId.HANDOFF_REVIEW,
            category=category,
            actions=(action,),
        )

    def _handoff(
        self,
        state: DialogueState,
        *,
        trigger: HandoffTrigger,
        reason_codes: tuple[ReasonCode, ...],
        template: TemplateId,
        category: DisputeCategory | None = None,
        verified_facts: tuple[EnvelopeTransactionFact, ...] = (),
        actions: tuple[ActionRecord, ...] = (),
        existing_case_number: str | None = None,
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Register a handoff and render its outcome, or ``HANDOFF_NOT_REGISTERED`` if it fails."""
        request = self._request
        principal = self._principal
        assert request is not None  # noqa: S101 - set at the top of handle_turn
        assert principal is not None  # noqa: S101 - set at the top of handle_turn
        content = HandoffContent(
            reference_date=self._domain_date,
            created_at=self._now(),
            language=state.lang,
            trigger=trigger,
            first_name=_UNKNOWN_FIRST_NAME,
            customer_id=principal.customer_id,
            request_summary=_REQUEST_SUMMARY_OF[trigger],
            reason_codes=reason_codes,
            policy_version=self._policy.version,
            category=category,
            verified_facts=verified_facts,
            actions=actions,
            existing_case_number=existing_case_number,
        )
        try:
            packet = self._outbox.record(
                content,
                session_id=state.session_id,
                turn_id=request.turn_id,
                trace_id=state.session_id,
            )
        except psycopg.Error:
            logger.warning("handoff_not_registered session_id=%s", state.session_id)
            new_state = state.with_phase(ConversationPhase.ABANDONED)
            return new_state, self._envelope(
                new_state, Intent.HANDOFF, TemplateId.HANDOFF_NOT_REGISTERED, end_session=True
            )

        new_state = state.with_handed_off(packet.ticket_ref)
        decisions = (_escalate_decision(self._policy),) if template in _ROUTED_HANDOFFS else ()
        facts = DisputeFacts(ticket_ref=packet.ticket_ref, category=category)
        return new_state, self._envelope(
            new_state, Intent.HANDOFF, template, facts=facts, decisions=decisions, end_session=True
        )

    # -------------------------------------------------------------------------------------
    # Replay of a duplicate turn
    # -------------------------------------------------------------------------------------

    def _replay_envelope(self, state: DialogueState) -> RenderEnvelope:
        """The reply for a turn id already applied to ``state`` — no further side effect.

        A filed case is read back fresh (its status may have moved on since); a handoff is
        rendered directly from its stored ticket; a pending clarification is a pure re-render.
        Every other outcome reachable here (ineligible, cancelled, farewell, a plain informational
        reply) has no tool call that is not already safe to repeat, so it is recomputed exactly as
        the original turn was, using the same, already-persisted facts.
        """
        if state.last_case_number is not None:
            case = dispatch(self._tool_port, tool_contracts.Tool.GET_CASE, state.last_case_number)
            if isinstance(case, ToolFailure) or case is None:
                return self._envelope(
                    state, Intent.HANDOFF, TemplateId.HANDOFF_NOT_REGISTERED, end_session=True
                )
            case_fact = to_envelope_case(case)
            facts = DisputeFacts(
                cases=(case_fact,), expected_response_on=case_fact.expected_response_on
            )
            return self._envelope(
                state, Intent.FILING_RESULT, TemplateId.FILING_RESULT, facts=facts
            )

        if state.last_ticket_ref is not None:
            facts = DisputeFacts(ticket_ref=state.last_ticket_ref, category=state.category)
            decisions = (_escalate_decision(self._policy),)
            return self._envelope(
                state,
                Intent.HANDOFF,
                TemplateId.HANDOFF_REVIEW,
                facts=facts,
                decisions=decisions,
                end_session=True,
            )

        if state.pending_slot is not None:
            return self._envelope(state, Intent.CLARIFY, _ASK_TEMPLATE_OF[state.pending_slot])

        request = self._request
        assert request is not None  # noqa: S101 - set at the top of handle_turn
        result = self._understanding.understand(request.text, language_hint=state.lang)
        _, envelope = self._advance(state, result)
        return envelope

    # -------------------------------------------------------------------------------------
    # Rendering
    # -------------------------------------------------------------------------------------

    def _envelope(
        self,
        state: DialogueState,
        intent: Intent,
        template: TemplateId,
        *,
        facts: DisputeFacts = _EMPTY_FACTS,
        decisions: tuple[Decision, ...] = (),
        sources: tuple[SourceRef, ...] = (),
        end_session: bool = False,
    ) -> RenderEnvelope:
        return RenderEnvelope(
            session_id=state.session_id,
            lang=state.lang,
            domain_date=self._domain_date,
            intent=intent,
            next_expected=state.pending_slot,
            end_session=end_session,
            facts=facts,
            decisions=decisions,
            sources=sources,
            render_mode="template",
            template_id=template,
        )

    def _respond(self, state: DialogueState, envelope: RenderEnvelope) -> TurnResponse:
        rendered: RenderedReply = render(envelope)
        request = self._request
        assert request is not None  # noqa: S101 - set at the top of handle_turn
        return TurnResponse(
            turn_id=request.turn_id,
            conversation_id=state.session_id,
            state_version=state.version,
            lang=state.lang,
            reply=rendered.reply,
            reference_date_line=rendered.reference_date_line,
            demo_notice=demo_notice(state.lang),
            next_expected=envelope.next_expected,
            end_session=envelope.end_session,
            handoff_ticket=state.last_ticket_ref if envelope.intent is Intent.HANDOFF else None,
        )


_Handler = Callable[
    [DialogueController, DialogueState, NluResult], tuple[DialogueState, RenderEnvelope]
]

# Exhaustive over NluIntent (tested): an intent added to the contract without a route here fails
# the tests, mirroring contracts.service_v1.envelope.TEMPLATE_INTENTS's own completeness idiom.
_ROUTES: dict[NluIntent, _Handler] = {
    NluIntent.FILE_DISPUTE: DialogueController._handle_file_dispute,
    NluIntent.LIST_TRANSACTIONS: DialogueController._handle_list_transactions,
    NluIntent.DISPUTE_STATUS: DialogueController._handle_dispute_status,
    NluIntent.POLICY_QUESTION: DialogueController._handle_policy_question,
    NluIntent.CONFIRMATION: DialogueController._handle_confirmation,
    NluIntent.CHOICE: DialogueController._handle_unroutable,
    NluIntent.CORRECTION: DialogueController._handle_unroutable,
    NluIntent.REPORT_FRAUD: DialogueController._handle_report_fraud,
    NluIntent.REPORT_CARD_LOSS: DialogueController._handle_report_card_loss,
    NluIntent.REQUEST_PERSON: DialogueController._handle_request_person,
    NluIntent.REQUEST_REVERSAL: DialogueController._handle_request_reversal,
    NluIntent.UNSUPPORTED_ACTION: DialogueController._handle_unsupported_action,
    NluIntent.SWITCH_LANGUAGE: DialogueController._handle_switch_language,
    NluIntent.SMALL_TALK: DialogueController._handle_small_talk,
    NluIntent.FAREWELL: DialogueController._handle_farewell,
    NluIntent.UNCLEAR: DialogueController._handle_unroutable,
}
