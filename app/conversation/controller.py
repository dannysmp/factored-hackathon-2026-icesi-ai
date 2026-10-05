"""
Dialogue Controller
====================

Overview
--------
Turns one customer message into a reply: it reads the conversation's state, asks the understanding
port what the message means, decides deterministically what happens next (never the model), calls
the scoped tools it needs, and renders the fixed-wording reply. The model understands and renders
language; this module decides and acts.

Scope
-----
In: ``DialogueController.handle_turn(request, *, principal) -> TurnResponse`` and everything it
takes to get there — routing by intent, the missing-slot guard, transaction search and matching,
policy evaluation, filing and its read-back verification, and escalation to a person.
Out: understanding a message (``app.conversation.understanding``), storing state
(``app.conversation.store``/``app.persistence.dialogue_store``), the tools themselves
(``app.tools``, ``app.persistence.reads``), rendering fixed wording
(``app.conversation.renderer``), the turns endpoint (``app.api.turns``) and the request body-size
cap (``app.security.middleware.BodySizeLimitMiddleware``).

Design Principles
-----------------
- **Built fresh per request.** Every collaborator is injected at construction; there is no module-
  level state and no singleton, so nothing about one customer's turn can leak into another's.
- **Unclear text before any dispute step asks for the transaction.** Only unclear text that
  arrives before any dispute step asks which transaction is meant; a greeting or thanks keeps the
  menu, and two unusable replies hand the conversation over with the missing element recorded.
- **One route per intent, exhaustively.** ``_ROUTES`` covers every ``NluIntent``, mirroring
  ``TEMPLATE_INTENTS``'s own completeness idiom: an intent added to the contract without a route
  here fails the tests, not silently falls through.
- **The guard decides what's missing; this module decides what to do about it.**
  ``app.conversation.guard.required_slot`` is the single source of truth for "is a transaction or a
  reason still missing"; identifying which transaction a given hint refers to (the search) is this
  module's own job, run whenever a hint is available and none is selected yet — independent of
  what the guard says about the *other* slot, since a hint is only ever available during the turn
  it was given (no raw text or hint is carried in ``DialogueState``).
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
- **A session has a turn cap.** Once ``max_turns`` customer turns are applied, the next one is
  answered with a handoff (or the session's existing ticket) without calling the understanding
  port, so a runaway or abusive session stops spending model calls.
- **Per-turn cost is logged, not stored**: a stable ``turn_completed`` log line reports the
  real model cost (if any — ``FakeNlu`` turns log zero/``None``) and, once the session has one, its
  case number, so cost per session or per case is computable from the log stream alone
  (``app.observability.turn_metrics``). Logged once the model call already happened, before the
  store save is attempted, since real spend occurred regardless of whether the save then replays
  or conflicts.
- **An unreachable dependency is not the customer's ambiguity.** ``Understanding.understand``
  raising ``UnderstandingUnavailable`` (the LLM port's own bounded retries and circuit breaker
  were exhausted) escalates directly to a handoff, saved with the same idempotent discipline as any
  other turn — it never reaches ``_advance``, so it never spends a clarification-budget attempt on
  an outage that was never the customer's own confusion. No accounting is logged for that attempt:
  nothing was priced.

Runtime Contract
----------------
``DialogueController(understanding, store, tool_port, retriever, policy, outbox, *, domain_date,
now, max_turns, model_renderer=None, turn_log=None)`` with ``handle_turn(request: TurnRequest, *,
principal: Principal) -> TurnResponse``. ``max_turns`` is the most customer turns a session may
apply; the next one is answered with a handoff and no understanding call. ``HandoffOutbox``
(protocol): the port this module writes a handoff through. ``model_renderer`` is ``None`` by
default (the fixed-wording template path only); passing an ``LlmRenderer`` lets eligible replies
render through the model path instead, verified, with the template as its own deterministic
fallback (``app.conversation.reply.render_reply``).
``DialogueTurnLog`` (protocol): the port this module records the console's own audit timeline
through; ``turn_log`` is ``None`` by default (nothing is recorded) and is never consulted on a
replayed turn, only on a turn this call genuinely advances.

Limitations
-----------
A single-match search result is presented with ``PRESENT_ONE`` and the customer's yes (or a
reason, which implies it) selects it; a no asks for the transaction again; naming a different
merchant, amount, card or date searches for that instead; an unclear answer asks again within the
clarification budget.

A yes that carries a change, or a correction, while the transaction or the filing is awaiting an
answer is not repeated back: the customer is asked which part to change, the transaction or the
reason. A different reason stated at the filing question re-evaluates the dispute under that
reason and answers with the policy's decision for it, asking for confirmation when the policy
requires one. A different transaction described there goes back to finding that transaction,
keeping the reason unless the message states another.
A description that changes neither repeats the filing question and counts against the clarification
budget.

The question stays pending across a reply to an unrelated message (small talk, a policy question, a
list request), as the reason and confirmation questions do, so the customer's yes after such a reply
still selects the presented transaction. The unrelated reply itself files nothing; a case is filed
only once the policy's confirmation requirement for the category is met. A list request shows the
customer's most recent transactions as numbered options and keeps their references, in order, in
``offered_refs``; a later number selects the transaction shown at that position, and its question
about the reason or the filing follows. A message that is only a number of one or two digits, sent
while the list is on offer, is read directly as that choice, without the model; a number past the
end of the list shows the list again, as does a choice the model reads past its end. Two or more
matches for a described transaction ask for more detail rather than presenting a numbered list.
A session works on one transaction and reason at a time: the selected pair is kept from selection
until the dispute ends (a case filed, or the filing cancelled, ineligible or refused as a
duplicate), which clears it so the customer's next dispute starts from its own transaction and
reason; a no to the transaction presented, a different transaction named at the
presented-transaction or filing question, or a number from a list just shown replaces the
transaction instead, and a different reason is evaluated afresh only at the filing question. A
transaction named at the filing question that is not found, or that matches several, leaves none
selected, so the customer describes the transaction again; nothing is filed in between. A dispute
that ends in a handoff keeps its pair. A policy question asked after a dispute has ended without a
handoff is answered without a reason, so a figure that depends on one is declined with an offer of
an advisor. The handoff packet's ``first_name`` is a placeholder: no tool exposes the customer's
first name.
While the transaction is the pending question, a message that describes one is taken as the
answer whichever intent the model reported (``correction``, ``choice`` or ``unclear``); a category
carried by such a message does not replace one already set. A description that matches no
transaction, or more than one, is an unsettled answer to the question, the same as any other reply
that leaves it open: a person is involved once the clarification budget of such answers has
followed the question. When the opening message already described the transaction, that message is
itself the question, so with the shipped budget of two the hand-off follows the third unmatched
description. A request to list transactions that finds none never
counts; a described transaction that finds none does, like any other unmatched description.
A duplicate turn's handoff replay always uses the generic reviewing wording, which may differ from
the original trigger-specific wording (fraud, card loss, a person requested) though it states the
same outcome and ticket. Contact-within-hours and structured risk evidence are not populated in a
handoff packet: neither is available from the tools this module calls. A genuine concurrent
duplicate (two requests racing on the same turn id, whether the session is brand new or already
has prior turns) each read the same starting state, each run their own real model call, and each
log their own ``turn_completed`` line before either attempts to save; the loser's save then
replays the winner's state, so one client-visible turn can log cost twice. This is an honest
account of both calls' real spend, not a bug in the log line itself, but it means "one
client-visible turn" and "one logged turn_completed line" are not always the same count under this
specific race.
"""

from __future__ import annotations

# Standard libraries
import hashlib  # Deterministic idempotency key derived from the turn id
import logging  # Progress events, never print
import unicodedata  # Accent-insensitive merchant comparison
from collections.abc import Callable  # Type of one route's handler
from datetime import date  # Domain date the controller was built with
from decimal import Decimal  # Money is never a float
from typing import Protocol  # The handoff outbox port this module depends on

# Third-party libraries
import psycopg  # Distinguishing an outbox write failure from every other outcome

# Local modules
from app.conversation.facts import to_envelope_case, to_envelope_transaction
from app.conversation.guard import required_slot
from app.conversation.handoff import HandoffContent
from app.conversation.model_renderer import LlmRenderer
from app.conversation.policy_answer import answer as policy_answer
from app.conversation.renderer import RenderedReply, demo_notice, transaction_line
from app.conversation.reply import render_reply
from app.conversation.state import ConversationPhase, DialogueState
from app.conversation.store import Conflict, DialogueStore, DuplicateTurn
from app.conversation.understanding import TurnAccounting, Understanding, UnderstandingUnavailable
from app.domain.policy.models import DisputeCategory, Outcome, Policy, PolicyDecision, ReasonCode
from app.llm.pricing import cost_usd  # Per-turn cost accounting
from app.retrieval.lexical import LexicalRetriever
from app.security.errors import ErrorCode, ProblemError
from app.security.middleware import current_request_id
from app.security.sessions import Clock, Principal
from app.tools.create_dispatch import create_dispute_case
from app.tools.dispatcher import dispatch
from contracts.service_v1 import tools as tool_contracts
from contracts.service_v1.api import Choice, TurnRequest, TurnResponse
from contracts.service_v1.cases import Money
from contracts.service_v1.console import TimelineEntry
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
from contracts.service_v1.handoff import ActionRecord, HandoffPacket, HandoffTrigger, OpenQuestion
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

# Stands in for the customer's first name, which no tool exposes (see Limitations): agent-facing
# only, never shown to the customer.
_UNKNOWN_FIRST_NAME = "Customer"

# Handoff templates whose reply states a routed (escalate) decision; the others (card loss, a
# person requested, an unregistered handoff, an unverified filing) carry no decision.
_ROUTED_HANDOFFS = frozenset({TemplateId.HANDOFF_REVIEW, TemplateId.HANDOFF_FRAUD})

# The facts of an envelope that states none.
_EMPTY_FACTS = DisputeFacts()

# Intents whose language says nothing reliable about the conversation's language: an unclear
# message, or one that already names the language it wants.
_LANGUAGE_NEUTRAL_INTENTS = frozenset({NluIntent.UNCLEAR, NluIntent.SWITCH_LANGUAGE})

# The open questions a change of mind can be answered under: the presented transaction and the
# filing awaiting confirmation. The reason question is asked afresh, never changed.
_CHANGEABLE_SLOTS = frozenset({Slot.TRANSACTION_CHOICE, Slot.CONFIRMATION})

# The most digits a message can have and still be read as a position in the list. A longer number
# is an amount, a card ending or a reference, which only the understanding step can place.
_MAX_LIST_NUMBER_DIGITS = 2

# The phases in which a conversation takes no further transaction. A conversation closed after its
# dispute ended without a handoff is not among them: the customer can list their transactions and
# pick one for the next dispute.
_PHASES_WITHOUT_SELECTION = frozenset({ConversationPhase.HANDED_OFF, ConversationPhase.ABANDONED})


def _number_from_list(state: DialogueState, text: str) -> NluResult | None:
    """The understanding of a message that is only a number, sent while a list is on offer.

    A number that is a position on the list is a choice; any other number is a request to see the
    list again, since it names nothing on it. A message that is not only a number, a number sent
    when no list is on offer, and any message once the conversation was handed to a person or
    abandoned are left to the understanding step.
    """
    stripped = text.strip()
    if (
        not state.offered_refs
        or state.phase in _PHASES_WITHOUT_SELECTION
        or not stripped.isascii()
        or not stripped.isdigit()
        or len(stripped) > _MAX_LIST_NUMBER_DIGITS
    ):
        return None
    number = int(stripped)
    if 1 <= number <= len(state.offered_refs):
        return NluResult(intent=NluIntent.CHOICE, confidence=1.0, choice=number)
    return NluResult(intent=NluIntent.LIST_TRANSACTIONS, confidence=1.0)


# The question template that asks for each slot, except the transaction choice, which is
# rendered by presenting the selected transaction again (``_transaction_choice_envelope``).
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
# words — never the customer's raw text (PII minimization). Exhaustive over
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


class DialogueTurnLog(Protocol):
    """Where a turn's own history is written; the port ``PostgresDialogueTurnLog`` implements."""

    def record(self, entry: TimelineEntry, *, session_id: str) -> None:
        """Write one turn's history row.

        Raises
        ------
        psycopg.Error
            The store could not be reached; the caller logs a warning and continues — losing this
            entry degrades the console's own view of the conversation, never the reply itself.
        """
        ...


def _idempotency_key(turn_id: str) -> str:
    """A deterministic key for ``create_dispute_case``, derived from the turn id.

    ``turn_id`` may be longer than the idempotency key's own 32-character bound, so it is hashed
    rather than passed through; the same turn always yields the same key, and a retried turn's
    filing call replays through the tool's own idempotency mechanism instead of filing twice.
    """
    return hashlib.sha256(turn_id.encode("utf-8")).hexdigest()[:32]


def _fold(text: str) -> str:
    """``text`` without accents and case, so "cafe" and "Café" compare equal."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


# What a customer calls a kind of transaction or of place rather than a merchant, folded: a
# merchant hint made only of one of these names nothing a transaction could be searched by. A
# transfer, for one, has no merchant at all, so the word describing it would rule it out.
_GENERIC_MERCHANTS = frozenset(
    {
        "transferencia",
        "transferencia bancaria",
        "transferencias",
        "transfer",
        "transfers",
        "bank transfer",
        "wire transfer",
        "pix",
        "transaccion",
        "transacciones",
        "transacao",
        "transacoes",
        "transaction",
        "transactions",
        "movimiento",
        "movimientos",
        "movimento",
        "movimentos",
        "cargo",
        "cargos",
        "cobro",
        "cobros",
        "cobranca",
        "cobrancas",
        "charge",
        "charges",
        "compra",
        "compras",
        "compra online",
        "compra en linea",
        "purchase",
        "purchases",
        "online purchase",
        "pago",
        "pagos",
        "pagamento",
        "pagamentos",
        "payment",
        "payments",
        "retiro",
        "retiros",
        "saque",
        "saques",
        "withdrawal",
        "withdrawals",
        "deposito",
        "depositos",
        "deposit",
        "deposits",
        "tienda",
        "tiendas",
        "tienda en linea",
        "tienda online",
        "comercio",
        "establecimiento",
        "estabelecimento",
        "loja",
        "lojas",
        "loja online",
        "loja virtual",
        "store",
        "stores",
        "online store",
        "shop",
        "shops",
        "online shop",
        "merchant",
    }
)

# Words that may open a generic merchant description ("una tienda en línea", "the store").
_LEADING_ARTICLES = frozenset({"un", "una", "el", "la", "o", "a", "um", "uma", "the", "an", "my"})


def _names_no_merchant(merchant: str) -> bool:
    """Whether ``merchant`` is empty or only a generic word for a kind of transaction or place."""
    folded = _fold(merchant)
    if not folded.strip():
        return True
    words = "".join(ch if ch.isalnum() else " " for ch in folded).split()
    while words and words[0] in _LEADING_ARTICLES:
        words.pop(0)
    return bool(words) and " ".join(words) in _GENERIC_MERCHANTS


def _without_unnamed_merchant(result: NluResult) -> NluResult:
    """``result`` with a merchant that names nothing removed from its transaction hint.

    A merchant that is empty once accents and surrounding blanks are removed, or that is only a
    generic word for a kind of transaction or place ("transferência", "tienda en línea"),
    describes nothing, so the rest of the hint (an amount, a date, a card) is what identifies the
    transaction.
    """
    merchant = result.transaction.merchant
    if merchant is None or not _names_no_merchant(merchant):
        return result
    return result.model_copy(
        update={"transaction": result.transaction.model_copy(update={"merchant": None})}
    )


def _agrees_with_amount_hint(money: Money, hint: TransactionHint) -> bool:
    """Whether ``money`` has the amount and the currency ``hint`` gives, those it does not give
    being unconstrained."""
    return (hint.amount is None or money.amount == hint.amount) and (
        hint.currency is None or money.currency == hint.currency
    )


def _matches_hint(fact: tool_contracts.TransactionFact, hint: TransactionHint) -> bool:
    """Whether ``fact`` could be what the customer described in ``hint``.

    Every part of ``hint`` that was given must agree; a part the source data cannot answer (an
    absent merchant and description, an unknown amount) never matches a hint that names it. The
    merchant is compared ignoring accents and case, in both directions: a customer who types
    "cafe" finds "Café Sol", and one who types "São Paulo" finds "SAO PAULO". A merchant that is
    empty once accents and surrounding blanks are removed names nothing, so it matches nothing.
    The amount and the currency must be those of one same figure: the amount in US dollars or the
    one in the currency the transaction was made in, so a figure quoted in pesos finds its
    transaction and a transaction with no dollar amount can still be found by its own.
    """
    if hint.merchant is not None:
        label = fact.merchant or fact.description
        wanted = _fold(hint.merchant).strip()
        if label is None or not wanted or wanted not in _fold(label):
            return False
    if hint.amount is not None or hint.currency is not None:
        figures = (fact.amount.money, fact.original_amount)
        if not any(m is not None and _agrees_with_amount_hint(m, hint) for m in figures):
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


def _unanswered(state: DialogueState, slot: Slot) -> OpenQuestion:
    """The element still missing when a conversation is handed over, and how often it was asked."""
    return OpenQuestion(slot=slot, attempts=state.clarification_attempts)


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
        max_turns: int,
        model_renderer: LlmRenderer | None = None,
        turn_log: DialogueTurnLog | None = None,
    ) -> None:
        """Wire one turn's collaborators; nothing is read or written yet.

        Parameters
        ----------
        understanding : Understanding
            Turns the customer's text into a structured result (a fake or the model-backed adapter).
        store : DialogueStore
            Where the session's state is read and saved between turns.
        tool_port : ToolPort
            The tool layer, already scoped to the authenticated customer.
        retriever : LexicalRetriever
            The policy-corpus search used to answer policy questions.
        policy : Policy
            The loaded policy; supplies the clarification budget and the policy version.
        outbox : HandoffOutbox
            Where a handoff packet is recorded.
        domain_date : date
            The injected business date used for date resolution and every rendered reference date.
        now : Clock
            The injected clock for timestamps.
        max_turns : int
            Customer turns a session may apply before every further one is handed to a person.
        model_renderer : LlmRenderer | None
            When given, eligible replies may render through the model path; ``None`` keeps templates
            only.
        turn_log : DialogueTurnLog | None
            When given, each advanced turn is recorded for the console's timeline.
        """
        self._understanding = understanding
        self._store = store
        self._tool_port = tool_port
        self._retriever = retriever
        self._policy = policy
        self._outbox = outbox
        self._domain_date = domain_date
        self._now = now
        self._max_turns = max_turns
        self._model_renderer = model_renderer
        self._turn_log = turn_log
        # Set once per call, at the top of handle_turn: every private helper below reads the
        # current turn's own request and principal from here rather than threading them through
        # every method signature. Safe because one instance ever handles exactly one turn.
        self._request: TurnRequest | None = None
        self._principal: Principal | None = None
        self._handoff_reason: ReasonCode | None = None

    # -------------------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------------------

    def handle_turn(self, request: TurnRequest, *, principal: Principal) -> TurnResponse:
        """Process one customer turn and return the reply.

        The flow is: load the session's state; a turn id already recorded on it is a replay and is
        answered without redoing anything unsafe; a conversation already handed to a person is
        answered with its ticket, whatever the message says, with no model call and nothing
        written, so a late confirmation can neither file a second case nor open a second ticket,
        and an unreachable understanding dependency cannot hand the same conversation off twice;
        a session at its turn cap is answered with a handoff and no model call; otherwise the
        message is understood, the outcome is decided and acted on (``_advance``), the cost line
        is logged, the new state is saved with optimistic concurrency, and the reply is rendered
        after the save.

        Below the cap, a conversation whose handoff could not be registered is answered with the
        notice that nothing was registered, whatever the message says, with no model call and
        nothing written, since no person has it and the question it left open can no longer be
        answered. At the cap the next message retries the registration.

        An unreachable understanding dependency becomes a handoff rather than a clarification
        attempt. A save that loses a race on the same turn id answers with the winner's result.

        Parameters
        ----------
        request : TurnRequest
            The customer's message and its client-chosen turn id.
        principal : Principal
            The authenticated session; its customer is the only one any tool call is scoped to.

        Returns
        -------
        TurnResponse
            The rendered reply, the saved state's version, and whether the session has ended.

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

        if current is not None:
            unadvanced = self._answer_without_advancing(current)
            if unadvanced is not None:
                return unadvanced

        try:
            state, expected_version, result, accounting = self._start_turn(current, request)
        except UnderstandingUnavailable:
            return self._handoff_from_turn(
                current, expected_version=current.version if current is not None else 0
            )
        state_before = state.phase
        new_state, envelope = self._advance(state, result)
        self._log_turn_decided(state, new_state, result, envelope)
        self._log_turn_completed(new_state, accounting)

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

        return self._respond(saved, envelope, state_before=state_before)

    def _answer_without_advancing(self, current: DialogueState) -> TurnResponse | None:
        """The reply for a turn that must not be understood or acted on, or ``None`` to proceed.

        A conversation already handed to a person is answered with its ticket; a session at its
        turn cap is handed off (or has its registration retried); a conversation whose handoff
        could not be registered, below the cap, is answered with the not-registered notice. None
        of these calls the model, and only the cap path writes.
        """
        if current.phase is ConversationPhase.HANDED_OFF and current.last_ticket_ref is not None:
            return self._respond(current, self._ticket_envelope(current))
        if current.turns_applied >= self._max_turns:
            return self._cap_reached(current)
        if current.phase is ConversationPhase.ABANDONED:
            logger.warning(
                "dialogue_abandoned_turn_refused session_id=%s request_id=%s",
                current.session_id,
                current_request_id(),
            )
            return self._respond(current, self._not_registered_envelope(current))
        return None

    def _not_registered_envelope(self, state: DialogueState) -> RenderEnvelope:
        """The notice that nothing was registered and the session has ended."""
        return self._envelope(
            state, Intent.HANDOFF, TemplateId.HANDOFF_NOT_REGISTERED, end_session=True
        )

    def _cap_reached(self, current: DialogueState) -> TurnResponse:
        """Answer a turn the session's turn cap refuses, without calling the model.

        A session that already has a handoff ticket gets that same ticket again and nothing is
        written, so a customer who keeps typing neither mints tickets nor advances the state. A
        session without one is handed to a person through the same idempotent save as any other
        handoff turn, carrying the case number it had already filed, if any. Its summary line is
        the generic low-understanding one; the ``turn_cap`` action record is what identifies it.

        Two concurrent requests at the cap on a session with no ticket can each record a packet
        before the save decides the winner (the outbox is idempotent per turn id, not per
        session), so the loser's packet stays in the queue unreferenced by the session. This is
        the same ordering every handoff turn has.
        """
        request = self._request
        assert request is not None  # noqa: S101 - set at the top of handle_turn
        logger.warning(
            "dialogue_turn_cap_reached session_id=%s turns_applied=%d max_turns=%d request_id=%s",
            current.session_id,
            current.turns_applied,
            self._max_turns,
            current_request_id(),
        )
        if current.last_ticket_ref is not None:
            return self._respond(current, self._ticket_envelope(current))
        new_state, envelope = self._handoff(
            current,
            trigger=HandoffTrigger.LOW_UNDERSTANDING,
            reason_codes=(),
            template=TemplateId.HANDOFF_REVIEW,
            actions=(ActionRecord(action="turn_cap", result="reached"),),
            existing_case_number=current.last_case_number,
        )
        try:
            saved = self._store.save(
                new_state,
                expected_version=current.version,
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
        return self._respond(saved, envelope, state_before=current.phase)

    def _handoff_from_turn(
        self, current: DialogueState | None, *, expected_version: int
    ) -> TurnResponse:
        """Escalate a turn that could not even be understood, because the understanding port's own
        dependency was unreachable after its bounded retries — never the customer's own
        ambiguity, so it skips ``_advance`` and its clarification-budget accounting entirely.

        Persists exactly like a normal turn advance: the same idempotent save, the same
        ``DuplicateTurn``/``Conflict`` handling, so a retried request behaves no differently than
        any other turn that happens to end in a handoff.
        """
        request = self._request
        assert request is not None  # noqa: S101 - set at the top of handle_turn
        logger.warning(
            "llm_understanding_unavailable_handoff session_id=%s request_id=%s",
            self._session_id(),
            current_request_id(),
        )
        base = current or DialogueState(
            session_id=self._session_id(),
            lang="es",
            phase=ConversationPhase.STARTED,
            updated_at=self._now(),
        )
        new_state, envelope = self._handoff(
            base,
            trigger=HandoffTrigger.TOOL_FAILURE,
            reason_codes=(),
            template=TemplateId.HANDOFF_REVIEW,
            actions=(ActionRecord(action="llm_understand", result="unavailable"),),
        )
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
        return self._respond(saved, envelope, state_before=base.phase)

    def _start_turn(
        self, current: DialogueState | None, request: TurnRequest
    ) -> tuple[DialogueState, int, NluResult, TurnAccounting | None]:
        """The state to advance from, the version it was read at, this message's understanding,
        and what understanding it cost (``None`` for ``FakeNlu`` or a call that did not complete).

        A message that is only a number, sent while a list of transactions is on offer, is read by
        ``_number_from_list`` without a model call: it needs no interpretation and costs nothing.

        A brand-new session starts at expected version 0 (a fresh insert, unconditional on it —
        ``DialogueStore.save``'s own documented behavior); its language is the first message's own,
        or Spanish when the message is too ambiguous to tell.
        While the conversation has not left its opening (``DialogueState.is_opening``) the next
        message read in another language moves it there, so a customer whose opener carried no
        language signal is answered in their own language from their first real message. A message
        the understanding could not make sense of, or one that asks for a language outright, never
        triggers that move: the first says nothing reliable about the language and the second
        already names it.
        """
        if current is not None:
            listed = _number_from_list(current, request.text)
            if listed is not None:
                return current, current.version, listed, None
            result, accounting = self._understanding.understand(
                request.text, language_hint=current.lang, reference_date=self._domain_date
            )
            if (
                current.is_opening
                and result.intent not in _LANGUAGE_NEUTRAL_INTENTS
                and result.language is not None
                and result.language != current.lang
            ):
                current = current.with_language(result.language)
            return current, current.version, result, accounting

        result, accounting = self._understanding.understand(
            request.text, language_hint=None, reference_date=self._domain_date
        )
        lang: Lang = result.language if result.language is not None else "es"
        fresh = DialogueState(
            session_id=self._session_id(),
            lang=lang,
            phase=ConversationPhase.STARTED,
            updated_at=self._now(),
        )
        return fresh, 0, result, accounting

    def _log_turn_decided(
        self,
        state_before: DialogueState,
        state_after: DialogueState,
        result: NluResult,
        envelope: RenderEnvelope,
    ) -> None:
        """One log line per turn that reaches the decision step, saying how it was understood and
        where the dialogue went, so a surprising hand-off can be traced to its cause without the
        customer's words.

        A replayed turn is silent, and so are the hand-offs that never reach the decision step
        (the turn cap, understanding unavailable), which log their own warnings. The line is
        emitted before the save, so a turn that then loses a concurrent save still logs one.

        It carries only closed-vocabulary values and counters: the intent the understanding
        reported and its confidence, whether it carried a transaction hint (a flag, never the
        hint), the pending slot and clarification count before and after, the reply's intent and
        template, and the hand-off's first reason code (``None`` when the turn did not hand off).
        """
        logger.info(
            "turn_decided session_id=%s understood=%s confidence=%.2f has_hint=%s "
            "slot_before=%s attempts_before=%d slot_after=%s attempts_after=%d "
            "reply=%s template=%s handoff_reason=%s",
            state_after.session_id,
            result.intent.value,
            result.confidence,
            not result.transaction.is_empty,
            state_before.pending_slot.value if state_before.pending_slot else None,
            state_before.clarification_attempts,
            state_after.pending_slot.value if state_after.pending_slot else None,
            state_after.clarification_attempts,
            envelope.intent.value,
            envelope.template_id.value if envelope.template_id else None,
            self._handoff_reason.value if self._handoff_reason else None,
        )

    def _log_turn_completed(self, state: DialogueState, accounting: TurnAccounting | None) -> None:
        """One stable-shaped log line per real turn: the real cost, if any, of understanding
        it, and which case (if any, by this point) the session belongs to.

        Emitted once the LLM call already happened, before the store save is attempted, so a real
        model cost is always logged even if the save then replays or conflicts — the spend already
        occurred regardless of what the client is told. Every field is present on every line,
        ``FakeNlu`` turns included, so the shape a log consumer parses never varies; only the
        values are zero/``None`` when no real call happened. A model the price table does not
        know about never aborts the turn: ``cost_usd`` raising is caught, a warning names the
        model, and this line logs ``cost_usd=None`` rather than propagating past the caller.
        """
        if accounting is None:
            model: str | None = None
            prompt_version: str | None = None
            input_tokens = 0
            output_tokens = 0
            latency_ms = 0.0
            cost: Decimal | None = Decimal(0)
        else:
            model = accounting.model
            prompt_version = accounting.prompt_version
            input_tokens = accounting.input_tokens
            output_tokens = accounting.output_tokens
            latency_ms = accounting.latency_ms
            try:
                cost = cost_usd(accounting.model, accounting.input_tokens, accounting.output_tokens)
            except KeyError:
                # An unpriced model must never abort the turn: the customer's own outcome (a
                # filed case, a handoff) does not depend on the cost log line completing. The
                # warning is what an operator sees to add the missing price.
                logger.warning("turn_cost_unpriced model=%s", accounting.model)
                cost = None
        logger.info(
            "turn_completed session_id=%s case_number=%s model=%s prompt_version=%s "
            "input_tokens=%s output_tokens=%s latency_ms=%s cost_usd=%s",
            state.session_id,
            state.last_case_number,
            model,
            prompt_version,
            input_tokens,
            output_tokens,
            latency_ms,
            cost,
        )

    def _session_id(self) -> str:
        """The authenticated session's id, read from the principal set for this turn."""
        assert self._principal is not None  # noqa: S101 - set at the top of handle_turn
        return self._principal.session_id

    def _turn_id(self) -> str:
        """The id of the turn being handled, read from the request set for this turn."""
        assert self._request is not None  # noqa: S101 - set at the top of handle_turn
        return self._request.turn_id

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
        """Collect what a filing needs, then evaluate it.

        While a filing awaits confirmation, a restated dispute is handled as a change to it.
        Otherwise keeps the category the message names (once, never overwritten), clears a pending
        transaction choice, then follows the guard: ask for the transaction when none is named,
        search for it when a hint is available and none is selected, ask for the reason, and
        finally evaluate the dispute for the selected transaction and category.
        """
        result = _without_unnamed_merchant(result)
        if state.pending_slot is Slot.CONFIRMATION:
            return self._handle_restated_dispute(state, result)

        if result.category is not None and state.category is None:
            state = state.model_copy(update={"category": result.category})

        if state.pending_slot is Slot.TRANSACTION_CHOICE:
            if self._names_another_transaction(state, result.transaction):
                state = state.model_copy(update={"selected_ref": None, "pending_slot": None})
            else:
                state = state.with_slot_filled()

        slot = required_slot(result, state)
        if slot is Slot.TRANSACTION:
            return self._ask(state, Slot.TRANSACTION)
        if state.selected_ref is None:
            return self._resolve_transaction(state, result.transaction)
        if slot is Slot.REASON:
            return self._ask(state, Slot.REASON)

        assert state.category is not None  # noqa: S101 - guaranteed by required_slot above
        return self._evaluate_and_present(state, state.selected_ref, state.category)

    def _handle_restated_dispute(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """The customer described a dispute while the filing question is open.

        A message naming a different transaction goes back to finding that transaction, keeping
        the reason unless the message states another. A message stating a different reason is
        evaluated afresh under it. A message that changes neither is a repeat of the question and
        counts against the clarification budget.
        """
        if self._names_another_transaction(state, result.transaction):
            category = result.category if result.category is not None else state.category
            reopened = state.model_copy(
                update={"selected_ref": None, "pending_slot": None, "category": category}
            )
            return self._resolve_transaction(reopened, result.transaction)
        if result.category is not None and result.category is not state.category:
            return self._handle_change(state, result)
        return self._ask(state, Slot.CONFIRMATION)

    def _names_another_transaction(self, state: DialogueState, hint: TransactionHint) -> bool:
        """Whether ``hint`` describes something other than the transaction just presented.

        A hint that names nothing, or only matches the presented transaction, is the customer
        going ahead with it. A hint that names a different merchant, amount, card or date means
        they rejected the one shown and are pointing at another. When the presented transaction
        cannot be read back, the hint is searched for afresh rather than assumed to match. The
        hint arrives with a merchant that names nothing already removed, so a customer who answers
        with only a word for a kind of transaction keeps the presented one, which the confirmation
        shows in full before a case is filed.
        """
        if hint.is_empty:
            return False
        assert state.selected_ref is not None  # noqa: S101 - set whenever this slot is pending
        selected = dispatch(
            self._tool_port, tool_contracts.Tool.GET_TRANSACTION, state.selected_ref
        )
        if isinstance(selected, ToolFailure) or selected is None:
            return True
        if hint.date_on is not None and hint.date_on != selected.occurred_on:
            return True
        return not _matches_hint(selected, hint)

    def _handle_list_transactions(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """List the customer's transactions.

        A tool failure becomes a handoff, which ends the conversation's automated handling. An
        empty list gets a not-found reply and leaves the state unchanged. A non-empty list records
        the references shown, in order, in ``offered_refs``.
        """
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
        offered = state.model_copy(
            update={"offered_refs": tuple(fact.ref for fact in facts.transactions)}
        )
        return offered, self._envelope(
            offered, Intent.PRESENT_TRANSACTIONS, TemplateId.PRESENT_LIST, facts=facts
        )

    def _handle_choice(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """The customer picked a number from the list just shown: that transaction is selected."""
        number = result.choice
        if state.phase in _PHASES_WITHOUT_SELECTION:
            return self._fallback(state, result)
        assert number is not None  # noqa: S101 - the contract reads a choice exactly for this intent
        if not 1 <= number <= len(state.offered_refs):
            if state.offered_refs:
                return self._handle_list_transactions(state, result)
            return self._handle_unroutable(state, result)
        selected = state.model_copy(
            update={
                "selected_ref": state.offered_refs[number - 1],
                "offered_refs": (),
                "pending_slot": None,
                "clarification_attempts": 0,
            }
        )
        assert selected.selected_ref is not None  # noqa: S101 - set on the line above
        if selected.category is None:
            return self._ask(selected, Slot.REASON)
        return self._evaluate_and_present(selected, selected.selected_ref, selected.category)

    def _handle_dispute_status(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Report the customer's dispute cases (at most the first three), or that there are none.

        A tool failure becomes a handoff. Nothing is written and the state does not change.
        """
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
        """Answer a policy question from the retrieved corpus, or abstain when nothing is found.

        The answer is the retrieved source plus the policy values it quotes; the state does not
        change.
        """
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
        """Route a yes or no by the question that is pending.

        A pending transaction choice and a pending filing confirmation each have a handler; a yes or
        no with nothing pending to answer is handled like an unroutable message: the menu, or the
        pending question again.
        """
        if state.pending_slot is Slot.TRANSACTION_CHOICE:
            return self._handle_transaction_choice(state, result)
        if state.pending_slot is Slot.CONFIRMATION:
            return self._handle_filing_confirmation(state, result)
        return self._fallback(state, result)

    def _handle_filing_confirmation(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """The customer's yes or no to filing the evaluated dispute."""
        answer = result.confirmation
        if answer is ConfirmationAnswer.NO:
            new_state = state.with_dispute_closed(self._turn_id())
            return new_state, self._envelope(new_state, Intent.CLARIFY, TemplateId.FILING_CANCELLED)
        if answer is ConfirmationAnswer.AMBIGUOUS:
            return self._ask(state, Slot.CONFIRMATION)
        if answer is not ConfirmationAnswer.YES:
            return self._handle_change(state, result)

        assert state.selected_ref is not None and state.category is not None  # noqa: S101
        decision = dispatch(
            self._tool_port,
            tool_contracts.Tool.EVALUATE_DISPUTE,
            EvaluateDisputeRequest(transaction_ref=state.selected_ref, category=state.category),
        )
        if isinstance(decision, ToolFailure) or decision is None:
            return self._tool_failure_handoff(state, tool=tool_contracts.Tool.EVALUATE_DISPUTE)
        if decision.outcome is not Outcome.ELIGIBLE:
            return self._present_non_eligible(state, state.category, decision)
        return self._file_and_verify(state, state.selected_ref, state.category, decision)

    def _handle_transaction_choice(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """The customer's yes or no to the one transaction just presented."""
        answer = result.confirmation
        if answer is ConfirmationAnswer.NO:
            return self._ask(
                state.model_copy(update={"selected_ref": None, "pending_slot": None}),
                Slot.TRANSACTION,
            )
        if answer is ConfirmationAnswer.AMBIGUOUS:
            return self._ask(state, Slot.TRANSACTION_CHOICE)
        if answer is not ConfirmationAnswer.YES:
            return self._handle_change(state, result)

        state = state.with_slot_filled()
        assert state.selected_ref is not None  # noqa: S101 - set whenever this slot is pending
        if state.category is None:
            return self._ask(state, Slot.REASON)
        return self._evaluate_and_present(state, state.selected_ref, state.category)

    def _handle_report_fraud(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Hand a fraud report to a person; the fraud claim is the escalation reason."""
        return self._handoff(
            state,
            trigger=HandoffTrigger.FRAUD_REPORT,
            reason_codes=(ReasonCode.ESCALATE_FRAUD_CLAIM,),
            template=TemplateId.HANDOFF_FRAUD,
        )

    def _handle_report_card_loss(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Hand a lost or stolen card report to a person."""
        return self._handoff(
            state,
            trigger=HandoffTrigger.CARD_LOSS,
            reason_codes=(),
            template=TemplateId.HANDOFF_CARD_LOSS,
        )

    def _handle_request_person(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Hand the conversation to a person at the customer's request."""
        return self._handoff(
            state,
            trigger=HandoffTrigger.CUSTOMER_REQUEST,
            reason_codes=(),
            template=TemplateId.HANDOFF_REQUESTED,
        )

    def _handle_request_reversal(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Refuse a request to reverse or refund a charge; the bank, not this service, decides."""
        return state, self._envelope(state, Intent.REFUSE, TemplateId.REFUSE_REVERSAL)

    def _handle_unsupported_action(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Refuse an action this service does not perform."""
        return state, self._envelope(state, Intent.REFUSE, TemplateId.REFUSE_UNSUPPORTED)

    def _handle_switch_language(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Continue the conversation in the language the customer asked for and greet them in it."""
        assert result.requested_language is not None  # noqa: S101 - guaranteed by the contract
        new_state = state.with_language(result.requested_language)
        return new_state, self._envelope(new_state, Intent.CLARIFY, TemplateId.GREETING)

    def _handle_small_talk(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Answer small talk with the greeting that offers what this service can do."""
        return state, self._envelope(state, Intent.CLARIFY, TemplateId.GREETING)

    def _handle_farewell(
        self, state: DialogueState, _result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """A farewell never advances ``state``: it carries no filing decision to protect from a
        replay, unlike ``ConversationPhase.CLOSED``, which every caller that sets it uses as the
        exclusive signal that a filing decision (ineligible, cancelled, duplicate) was reached and
        must never be recomputed (see ``_replay_envelope``)."""
        return state, self._envelope(state, Intent.FAREWELL, TemplateId.FAREWELL, end_session=True)

    def _handle_unroutable(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """A ``correction`` outside the questions a change of mind can answer shares ``unclear``'s
        fallback, and so does a ``choice`` that names no listed transaction, except when the
        transaction is what was just asked for and the message describes one: the model reads each
        message on its own, so a plain answer to that question can come back under any of these
        intents, and the description is the answer."""
        if state.pending_slot is Slot.TRANSACTION and not result.transaction.is_empty:
            return self._handle_file_dispute(state, result)
        return self._fallback(state, result)

    def _handle_unclear(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Text that could not be understood, before any dispute step, asks which transaction it
        is about, naming the missing element, rather than repeating the menu; once a dispute has
        ended, or while a question is open, it is handled as any message that fits no intent. A
        greeting or small talk has its own handler and keeps the menu."""
        if state.pending_slot is None and state.phase is ConversationPhase.STARTED:
            return self._ask(state, Slot.TRANSACTION)
        return self._handle_unroutable(state, result)

    def _handle_correction(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """A correction while a question about the presented transaction or the filing is open
        is handled as a change to it; anywhere else it is handled like an unroutable message, so a
        description of the transaction that was just asked for is still taken as the answer."""
        if state.pending_slot in _CHANGEABLE_SLOTS:
            return self._handle_change(state, result)
        return self._handle_unroutable(state, result)

    def _handle_change(
        self, state: DialogueState, result: NluResult
    ) -> tuple[DialogueState, RenderEnvelope]:
        """The customer answered the open question with a change rather than a plain yes or no.

        When the filing is awaiting confirmation and the message states a different reason, the
        dispute is evaluated afresh under that reason and answered with the policy's decision:
        the customer is asked to confirm only when the policy requires it. Otherwise the customer
        is asked which part to change, the transaction or the reason, and the repeat counts
        against the clarification budget like any other unanswered question.
        """
        assert state.pending_slot is not None  # noqa: S101 - both callers run with a slot open
        if (
            state.pending_slot is Slot.CONFIRMATION
            and result.category is not None
            and result.category is not state.category
        ):
            assert state.selected_ref is not None  # noqa: S101 - set whenever this slot is pending
            changed = state.with_slot_filled().model_copy(update={"category": result.category})
            return self._evaluate_and_present(changed, state.selected_ref, result.category)
        return self._ask(state, state.pending_slot, template=TemplateId.CLARIFY_CHANGE)

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

    def _ask(
        self, state: DialogueState, slot: Slot, *, template: TemplateId | None = None
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Ask again for ``slot``, or escalate once the clarification budget is spent.

        ``template`` replaces the slot's own wording with a clarification that carries no facts.
        """
        new_state = state.with_clarification(slot)
        if new_state.clarification_attempts >= self._policy.routing.clarification_budget:
            return self._handoff(
                new_state,
                trigger=HandoffTrigger.LOW_UNDERSTANDING,
                reason_codes=(ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,),
                template=TemplateId.HANDOFF_REVIEW,
                open_questions=(_unanswered(new_state, slot),),
            )
        if template is not None:
            return new_state, self._envelope(new_state, Intent.CLARIFY, template)
        if slot is Slot.TRANSACTION_CHOICE:
            envelope = self._transaction_choice_envelope(new_state)
            if envelope is None:
                return self._tool_failure_handoff(
                    new_state, tool=tool_contracts.Tool.GET_TRANSACTION
                )
            return new_state, envelope
        return new_state, self._envelope(new_state, Intent.CLARIFY, _ASK_TEMPLATE_OF[slot])

    def _present_selected(
        self, state: DialogueState, fact: EnvelopeTransactionFact
    ) -> RenderEnvelope:
        """Present the one transaction found, asking the customer to say it is the right one."""
        facts = DisputeFacts(transactions=(fact,), candidate_count=1, selected_ref=fact.ref)
        return self._envelope(
            state, Intent.PRESENT_TRANSACTIONS, TemplateId.PRESENT_ONE, facts=facts
        )

    def _transaction_choice_envelope(self, state: DialogueState) -> RenderEnvelope | None:
        """The selected transaction presented again, read fresh; ``None`` if it cannot be read."""
        assert state.selected_ref is not None  # noqa: S101 - set whenever this slot is pending
        transaction = dispatch(
            self._tool_port, tool_contracts.Tool.GET_TRANSACTION, state.selected_ref
        )
        if isinstance(transaction, ToolFailure) or transaction is None:
            return None
        return self._present_selected(state, to_envelope_transaction(transaction))

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
            return self._ask_for_a_better_description(state, TemplateId.NOT_FOUND)
        if len(matches) > 1:
            return self._ask_for_a_better_description(state, TemplateId.PRESENT_NARROW)

        fact = to_envelope_transaction(matches[0])
        new_state = state.model_copy(
            update={
                "selected_ref": fact.ref,
                "offered_refs": (),
                "pending_slot": Slot.TRANSACTION_CHOICE,
                "clarification_attempts": 0,
            }
        )
        return new_state, self._present_selected(new_state, fact)

    def _ask_for_a_better_description(
        self, state: DialogueState, template: TemplateId
    ) -> tuple[DialogueState, RenderEnvelope]:
        """The description matched no transaction, or more than one: ask for it again, or hand
        over once the clarification budget is spent. Each such reply is an unsettled answer to the
        transaction question; the count is zero on the first ask, so the budget is reached by the
        second answer that follows a question already asked."""
        new_state = state.with_clarification(Slot.TRANSACTION)
        if new_state.clarification_attempts >= self._policy.routing.clarification_budget:
            return self._handoff(
                new_state,
                trigger=HandoffTrigger.LOW_UNDERSTANDING,
                reason_codes=(ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,),
                template=TemplateId.HANDOFF_REVIEW,
                open_questions=(_unanswered(new_state, Slot.TRANSACTION),),
            )
        return new_state, self._envelope(new_state, Intent.CLARIFY, template)

    def _evaluate_and_present(
        self, state: DialogueState, ref: str, category: DisputeCategory
    ) -> tuple[DialogueState, RenderEnvelope]:
        """Ask the policy engine for a decision and act on its outcome.

        Not eligible: present the refusal or escalate. Eligible without a confirmation requirement:
        file now. Eligible and needing a confirmation: read the transaction fresh and ask the
        customer to confirm, leaving the confirmation slot pending. A failed tool call becomes a
        handoff.
        """
        decision = dispatch(
            self._tool_port,
            tool_contracts.Tool.EVALUATE_DISPUTE,
            EvaluateDisputeRequest(transaction_ref=ref, category=category),
        )
        if isinstance(decision, ToolFailure) or decision is None:
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
        """Present a decision that is not eligible.

        An ineligible decision closes the conversation with the refusal reason the customer may be
        told. An escalation hands off with the trigger that matches its reason code and the full
        list of codes, using the fraud wording when the reason is a fraud claim.
        """
        if decision.outcome is Outcome.INELIGIBLE:
            new_state = state.with_dispute_closed(self._turn_id())
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
        """File the dispute, read it back, and report it only once the read-back agrees.

        The filing uses an idempotency key derived from the turn id, so a retried turn never files
        twice. A refusal is presented by ``_present_creation_refusal``; a failed call is a
        tool-failure handoff. The case the tool reports is then read through ``get_case``: when that
        read fails or its transaction or category differs from what was filed, the outcome is a
        handoff for an unverified filing that names the case number, and the customer is not told it
        succeeded.
        """
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
        """Present a refused filing.

        A duplicate open case closes the conversation as ineligible for that reason. Any other
        refusal is handed to a person, recording the refusal code and the existing case number when
        the tool gave one.
        """
        if refusal is ToolRefusalCode.DUPLICATE_OPEN_CASE:
            new_state = state.with_dispute_closed(self._turn_id())
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
        """Hand off because a tool call failed, recording which tool failed.

        The customer is told a person will review it; the action record names the tool, not the
        error.
        """
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
        open_questions: tuple[OpenQuestion, ...] = (),
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
            open_questions=open_questions,
        )
        try:
            packet = self._outbox.record(
                content,
                session_id=state.session_id,
                turn_id=request.turn_id,
                trace_id=state.session_id,
            )
        except psycopg.Error:
            logger.warning(
                "handoff_not_registered session_id=%s request_id=%s",
                state.session_id,
                current_request_id(),
            )
            new_state = state.with_slot_filled().with_phase(ConversationPhase.ABANDONED)
            return new_state, self._envelope(
                new_state, Intent.HANDOFF, TemplateId.HANDOFF_NOT_REGISTERED, end_session=True
            )

        self._handoff_reason = reason_codes[0] if reason_codes else None
        new_state = state.with_handed_off(packet.ticket_ref)
        decisions = (_escalate_decision(self._policy),) if template in _ROUTED_HANDOFFS else ()
        facts = DisputeFacts(ticket_ref=packet.ticket_ref, category=category)
        return new_state, self._envelope(
            new_state, Intent.HANDOFF, template, facts=facts, decisions=decisions, end_session=True
        )

    # -------------------------------------------------------------------------------------
    # Replay of a duplicate turn
    # -------------------------------------------------------------------------------------

    def _ticket_envelope(self, state: DialogueState) -> RenderEnvelope:
        """The handoff reply for the ticket ``state`` already holds."""
        facts = DisputeFacts(ticket_ref=state.last_ticket_ref, category=state.category)
        return self._envelope(
            state,
            Intent.HANDOFF,
            TemplateId.HANDOFF_REVIEW,
            facts=facts,
            decisions=(_escalate_decision(self._policy),),
            end_session=True,
        )

    def _replay_envelope(self, state: DialogueState) -> RenderEnvelope:
        """The reply for a turn id already applied to ``state`` — no further side effect.

        A filed case is read back fresh (its status may have moved on since); a handoff is
        rendered directly from its stored ticket; a pending clarification is a pure re-render.
        The ticket takes precedence over a case when the session is handed off or filed no case,
        so a session holding both replays its handoff, which is the latest outcome but not
        necessarily the one the replayed turn id originally produced. A question still open
        comes before a filed case: a filing leaves nothing pending, so an open question was
        asked by a later turn and is what a retry of that turn is owed. A retried turn id that
        followed the filing and left no question open replays the filing result, unless the turn
        is the one that closed a later dispute without filing (``closed_turn_id``), which is
        answered with that closing, not with the case filed before it. A conversation whose
        handoff could not be registered replays the notice that nothing was registered, never
        the question it left open, since that question can no longer be answered.
        ``ConversationPhase.CLOSED`` is the exclusive signal that a filing decision (ineligible,
        cancelled, duplicate) was reached with nothing to show for it: every caller that sets it
        clears the pending slot and leaves no ticket behind, so it can never be confused with a
        plain conversational ending here, and the turn that closed it is recorded on the state
        because a case filed earlier stays there. It renders a generic, truthful acknowledgment
        rather than recomputing, because recomputing would call ``evaluate_dispute`` again — a
        fresh read against the store's *current* facts, not the ones the original decision rested
        on, so a fact that changed since (a filing window that closed, a case opened through
        another channel) could silently turn a past refusal into a filing with no new confirmation
        from the customer. Every other outcome reachable here (a plain informational reply, a
        farewell, which touches no state at all) has no such decision to protect and no tool call
        that is not already safe to repeat, so it is recomputed exactly as the original turn was.
        """
        if state.last_ticket_ref is not None and (
            state.phase is ConversationPhase.HANDED_OFF or state.last_case_number is None
        ):
            return self._ticket_envelope(state)

        if state.phase is ConversationPhase.ABANDONED:
            return self._not_registered_envelope(state)

        if state.pending_slot is not None:
            return self._replay_pending(state, state.pending_slot)

        if state.last_case_number is not None and state.closed_turn_id != state.last_turn_id:
            return self._filed_case_envelope(state, state.last_case_number)

        if state.phase is ConversationPhase.CLOSED:
            return self._envelope(state, Intent.CLARIFY, TemplateId.FILING_CANCELLED)

        return self._replay_recompute(state)

    def _filed_case_envelope(self, state: DialogueState, case_number: str) -> RenderEnvelope:
        """The filing result for ``case_number`` read back fresh, or the not-registered notice
        when the case cannot be read."""
        case = dispatch(self._tool_port, tool_contracts.Tool.GET_CASE, case_number)
        if isinstance(case, ToolFailure) or case is None:
            return self._not_registered_envelope(state)
        case_fact = to_envelope_case(case)
        facts = DisputeFacts(
            cases=(case_fact,), expected_response_on=case_fact.expected_response_on
        )
        return self._envelope(state, Intent.FILING_RESULT, TemplateId.FILING_RESULT, facts=facts)

    def _replay_pending(self, state: DialogueState, slot: Slot) -> RenderEnvelope:
        """The question still open, rendered again."""
        if slot is not Slot.TRANSACTION_CHOICE:
            return self._envelope(state, Intent.CLARIFY, _ASK_TEMPLATE_OF[slot])
        return self._transaction_choice_envelope(state) or self._envelope(
            state, Intent.HANDOFF, TemplateId.HANDOFF_NOT_REGISTERED, end_session=True
        )

    def _replay_recompute(self, state: DialogueState) -> RenderEnvelope:
        """Recompute a replayed turn's reply exactly as the original turn was, unless the
        understanding port's own dependency is unreachable right now: that failure is not
        the customer's ambiguity, and recomputing it needs the same dependency that just failed,
        so it renders a generic acknowledgment instead of recomputing — without touching persisted
        state or writing a new outbox row, since replay never mutates state and a retried replay
        call is free to try recomputing again once the outage clears.
        """
        request = self._request
        assert request is not None  # noqa: S101 - set at the top of handle_turn
        # A replay's own re-understanding is not a new turn (per-turn accounting is scoped to
        # handle_turn's own call in _start_turn); its accounting, if any, is not logged again here.
        # Only the "show the list again" reading is taken from the number: an in-range number
        # always clears the list on the turn it selects, so a list still on offer beside one was
        # left by a turn the model read, which the model reads again.
        result = _number_from_list(state, request.text)
        if result is not None and result.intent is not NluIntent.LIST_TRANSACTIONS:
            result = None
        try:
            if result is None:
                result, _replay_accounting = self._understanding.understand(
                    request.text, language_hint=state.lang, reference_date=self._domain_date
                )
        except UnderstandingUnavailable:
            logger.warning(
                "llm_understanding_unavailable_replay session_id=%s request_id=%s",
                state.session_id,
                current_request_id(),
            )
            return self._envelope(
                state, Intent.HANDOFF, TemplateId.HANDOFF_NOT_REGISTERED, end_session=True
            )
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
        """Build the render envelope for ``template`` from ``state``.

        The envelope carries the session's language, the injected domain date and the slot still
        pending (``next_expected``); it is always template mode here, the model path being chosen
        later by ``render_reply``. Pure: no state is changed.
        """
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

    def _respond(
        self,
        state: DialogueState,
        envelope: RenderEnvelope,
        *,
        state_before: ConversationPhase | None = None,
    ) -> TurnResponse:
        """Render ``envelope`` and build the turn response from the saved ``state``.

        Rendering goes through ``render_reply`` (templates, or the verified model path when a model
        renderer was injected). When ``state_before`` is given the turn's history is recorded;
        a turn that passes no ``state_before`` (a replay, a repeated turn-cap answer, or the notice
        given to a conversation whose handoff was not registered) records nothing. The handoff
        ticket is included only on a handoff reply.
        """
        rendered: RenderedReply = render_reply(envelope, model_renderer=self._model_renderer)
        request = self._request
        assert request is not None  # noqa: S101 - set at the top of handle_turn
        if state_before is not None:
            self._record_turn(state, envelope, rendered, state_before)
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
            choices=_choices_of(envelope),
            handoff_ticket=state.last_ticket_ref if envelope.intent is Intent.HANDOFF else None,
            case_number=state.last_case_number if envelope.intent is Intent.FILING_RESULT else None,
        )

    def _record_turn(
        self,
        state: DialogueState,
        envelope: RenderEnvelope,
        rendered: RenderedReply,
        state_before: ConversationPhase,
    ) -> None:
        """Record this turn's history for the console's timeline; never on a replay, and never
        affecting the reply already computed.

        Nothing is written when no turn log was injected.

        ``reason_code`` is the first reason code of the handoff this turn registered, and ``None``
        for a turn that did not hand off: the domain ``ReasonCode`` behind a policy decision is not
        carried on ``Decision``, so only handoffs name theirs. A failed database write is logged as
        a warning and swallowed; any other error propagates.
        """
        if self._turn_log is None:
            return
        request = self._request
        assert request is not None  # noqa: S101 - set at the top of handle_turn
        entry = TimelineEntry(
            occurred_at=self._now(),
            trace_id=state.session_id,
            turn_id=request.turn_id,
            intent=envelope.intent,
            state_before=state_before.value,
            state_after=state.phase.value,
            render_mode=rendered.render_mode,
            reason_code=self._handoff_reason,
            policy_version=envelope.decisions[0].policy_version if envelope.decisions else None,
        )
        try:
            self._turn_log.record(entry, session_id=state.session_id)
        except psycopg.Error:
            logger.warning(
                "dialogue_turn_not_logged session_id=%s request_id=%s",
                state.session_id,
                current_request_id(),
            )


def _choices_of(envelope: RenderEnvelope) -> tuple[Choice, ...]:
    """The numbered options a list reply offers, one per transaction shown, in the order shown."""
    if envelope.template_id is not TemplateId.PRESENT_LIST:
        return ()
    return tuple(
        Choice(number=number, label=transaction_line(transaction, envelope.lang))
        for number, transaction in enumerate(envelope.facts.transactions, start=1)
    )


# One route's handler: the unbound method, called with the controller, state and understanding.
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
    NluIntent.CHOICE: DialogueController._handle_choice,
    NluIntent.CORRECTION: DialogueController._handle_correction,
    NluIntent.REPORT_FRAUD: DialogueController._handle_report_fraud,
    NluIntent.REPORT_CARD_LOSS: DialogueController._handle_report_card_loss,
    NluIntent.REQUEST_PERSON: DialogueController._handle_request_person,
    NluIntent.REQUEST_REVERSAL: DialogueController._handle_request_reversal,
    NluIntent.UNSUPPORTED_ACTION: DialogueController._handle_unsupported_action,
    NluIntent.SWITCH_LANGUAGE: DialogueController._handle_switch_language,
    NluIntent.SMALL_TALK: DialogueController._handle_small_talk,
    NluIntent.FAREWELL: DialogueController._handle_farewell,
    NluIntent.UNCLEAR: DialogueController._handle_unclear,
}
