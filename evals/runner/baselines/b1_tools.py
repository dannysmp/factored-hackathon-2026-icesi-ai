"""
B1 Tool Schemas and Dispatch
============================

Overview
--------
The seven tools B1 (the naive agent baseline) may call, as Anthropic tool schemas: the six real
``ToolPort`` methods plus ``get_policy`` and ``handoff``. ``B1ToolDispatcher`` turns one model tool
call into a real effect against the store, retriever and handoff outbox that P and B0 use. B1 gets
the same tools as P and no wider surface, which the comparison depends on.

Scope
-----
In: the seven tool schemas; ``B1ToolDispatcher.dispatch``, executing one call and returning the
tool-result text the model reads next.
Out: the conversation loop that decides when to call the model again and when a customer turn is
done (``evals.runner.baselines.b1``); scoring a run (``evals.scoring``).

Design Principles
-----------------
- **The model decides; this module's code writes.** Every persisted, content-bearing field of a
  handoff packet (``request_summary``, ``reason_codes``, ``first_name``, ``policy_version``) is
  built by the dispatcher from facts it holds, never taken from the model's tool-call arguments. The
  tool schema declares no parameter for any of them, so they are inexpressible, not merely
  forbidden, as with ``create_dispute_case``. The model's only freedom is whether to hand off and
  which named trigger to cite, so the packet a human reads is as disciplined for B1 as for P.
- **``create_dispute_case`` uses the decision the dispatcher holds, never the model's.** The model
  can request a filing by transaction and category; the ``PolicyDecision`` the filing must match is
  the one the dispatcher's own most recent ``evaluate_dispute`` call for that transaction and
  category produced, the rule ``CreateDisputeCaseRequest.decision`` enforces for every caller. A
  filing request with no matching decision is passed on with ``decision=None`` and refused by the
  tool port.
- **``get_policy`` returns raw corpus text, not the policy engine's injected figures.** It wraps
  ``Retriever.search`` directly and not ``app.conversation.policy_answer.answer``, which also
  injects policy-engine-owned numbers (filing-window days, evidence lists) by category before P
  renders them. Giving B1 that injection would hand it P's grounding discipline and defeat the
  comparison; B1 must read any figure out of corpus prose, as a free-form model would. This is
  safe only because the corpus is generated from the policy file the engine enforces, whose drift
  check keeps the two in agreement; that is a property of corpus generation, not of this tool.
- **One decision path, duplicated by necessity.** ``_REQUEST_SUMMARY_OF`` mirrors the private table
  of ``app.conversation.controller`` (one entry per ``HandoffTrigger``) because that table is not
  exported, and a test asserts the two are equal.

Runtime Contract
----------------
``TOOL_SCHEMAS``: the seven Anthropic tool schemas, in a fixed order.
``B1ToolDispatcher(tool_port, retriever, outbox, policy, calendar, clock, *, customer_id, lang)``.
``dispatch(call, *, session_id, turn_id, trace_id) -> str``, the tool-result text for the model.
``start_turn()``: clears the list of this turn's decisions that ``handoff``'s reason-code lookup
reads, the last turn's handoff ticket and the last confirmable decision; the conversation loop
calls it once per customer turn, before that turn's tool-call rounds.
``handoff_ticket``: the current turn's handoff ticket reference, or ``None``.
``last_confirmable_decision``: this turn's last eligible ``evaluate_dispute`` decision, or ``None``
once a filing attempt follows it or none has happened; the loop reads it to build
``evals.scoring.RunTranscript.confirmed_target``, the fact ``dialogue_state`` names for a system
reached over HTTP.

Limitations
-----------
``first_name`` is always the ``_UNKNOWN_FIRST_NAME`` placeholder that P and B0 also use: no tool
exposes a customer's first name, and B1 gets no capability the other systems lack.
"""

from __future__ import annotations

# Standard libraries
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field

# Local modules
from app.conversation.controller import HandoffOutbox  # The port, same one P's controller takes
from app.conversation.handoff import HandoffContent
from app.domain.calendar import DomainCalendar  # The reference date B1 holds, injected like P's
from app.domain.policy.models import DisputeCategory, Outcome, Policy, PolicyDecision, ReasonCode
from app.retrieval.lexical import Retriever
from app.security.sessions import Clock
from app.tools.dispatcher import dispatch as dispatch_tool_port
from contracts.service_v1.envelope import Lang
from contracts.service_v1.handoff import HandoffTrigger
from contracts.service_v1.tools import (
    CreateDisputeCaseRequest,
    EvaluateDisputeRequest,
    Tool,
    ToolFailure,
    ToolPort,
    TransactionFilters,
)
from evals.runner.baselines.naive_agent_client import ToolCall

_UNKNOWN_FIRST_NAME = "Customer"

# Mirrors ``app.conversation.controller._REQUEST_SUMMARY_OF`` exactly; see the module's Design
# Principles for why this is a tested copy rather than an import.
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

# The closed value sets the schemas offer the model as enums.
_CATEGORY_VALUES = [category.value for category in DisputeCategory]
_TRIGGER_VALUES = [trigger.value for trigger in HandoffTrigger]

#: The tool schemas handed to the model, in a fixed order: the six ``ToolPort`` tools, then
#: ``get_policy`` and ``handoff``. No schema carries a customer identifier.
TOOL_SCHEMAS: tuple[dict[str, object], ...] = (
    {
        "name": "list_transactions",
        "description": "List the customer's own transactions, most recent first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "ISO date, optional lower bound."},
                "until": {"type": "string", "description": "ISO date, optional upper bound."},
            },
        },
    },
    {
        "name": "get_transaction",
        "description": "Look up one of the customer's own transactions by its reference.",
        "input_schema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
        },
    },
    {
        "name": "list_dispute_cases",
        "description": "List every dispute case the customer has filed.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_case",
        "description": "Look up one of the customer's own dispute cases by its case number.",
        "input_schema": {
            "type": "object",
            "properties": {"case_number": {"type": "string"}},
            "required": ["case_number"],
        },
    },
    {
        "name": "evaluate_dispute",
        "description": "Check whether a transaction is eligible for a dispute in one category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "transaction_ref": {"type": "string"},
                "category": {"type": "string", "enum": _CATEGORY_VALUES},
            },
            "required": ["transaction_ref", "category"],
        },
    },
    {
        "name": "create_dispute_case",
        "description": (
            "File a dispute case, after the customer has explicitly confirmed it, for a "
            "transaction and category already checked eligible by evaluate_dispute."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "transaction_ref": {"type": "string"},
                "category": {"type": "string", "enum": _CATEGORY_VALUES},
                "confirmed": {
                    "type": "boolean",
                    "description": "True only if the customer explicitly said yes.",
                },
            },
            "required": ["transaction_ref", "category", "confirmed"],
        },
    },
    {
        "name": "get_policy",
        "description": "Search the dispute policy corpus for the sections that answer a question.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "handoff",
        "description": (
            "Escalate the conversation to a person. Use this instead of trying to resolve "
            "something yourself when the customer reports fraud or a lost card, asks for a "
            "person directly, or a tool you called failed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"trigger": {"type": "string", "enum": _TRIGGER_VALUES}},
            "required": ["trigger"],
        },
    },
)


def _str_arg(call: ToolCall, name: str) -> str:
    """The argument ``name`` of ``call`` as a string; the schema marks it required."""
    return str(call.input[name])


def _idempotency_key(turn_id: str) -> str:
    """The idempotency key for ``turn_id``, derived as ``app.conversation.controller`` does.

    A ``turn_id`` can exceed the key's 32-character bound, so it is hashed, not passed through.
    """
    return hashlib.sha256(turn_id.encode("utf-8")).hexdigest()[:32]


# The four tools with no side effect and no state to track across calls, as a closed mapping that
# ``dispatch`` consults before the stateful tools.
_READ_TOOLS: dict[str, Callable[[ToolPort, ToolCall], object]] = {
    "list_transactions": lambda port, call: dispatch_tool_port(
        port,
        Tool.LIST_TRANSACTIONS,
        TransactionFilters(since=call.input.get("since"), until=call.input.get("until")),
    ),
    "get_transaction": lambda port, call: dispatch_tool_port(
        port, Tool.GET_TRANSACTION, _str_arg(call, "ref")
    ),
    "list_dispute_cases": lambda port, _call: dispatch_tool_port(port, Tool.LIST_DISPUTE_CASES),
    "get_case": lambda port, call: dispatch_tool_port(
        port, Tool.GET_CASE, _str_arg(call, "case_number")
    ),
}


@dataclass
class B1ToolDispatcher:
    """Executes one model tool call at a time against the real store, retriever and outbox.

    One instance serves one case: it is scoped to a customer and language, and tracks the
    ``evaluate_dispute`` decisions it has produced so a filing can be matched to one. The two
    non-``init`` fields are the harness's read-outs: the turn's handoff ticket and its last
    confirmable decision.
    """

    tool_port: ToolPort
    retriever: Retriever
    outbox: HandoffOutbox
    policy: Policy
    calendar: DomainCalendar
    clock: Clock
    customer_id: str
    lang: Lang
    handoff_ticket: str | None = field(default=None, init=False)
    last_confirmable_decision: PolicyDecision | None = field(default=None, init=False)
    _decisions: dict[tuple[str, DisputeCategory], PolicyDecision] = field(
        default_factory=dict, init=False
    )
    _decisions_this_turn: list[PolicyDecision] = field(default_factory=list, init=False)

    def start_turn(self) -> None:
        """Clear the per-turn state: this turn's decisions, the handoff ticket and the last
        confirmable decision, so none of them carries into a later turn's reply."""
        self._decisions_this_turn = []
        self.handoff_ticket = None
        self.last_confirmable_decision = None

    def dispatch(self, call: ToolCall, *, session_id: str, turn_id: str, trace_id: str) -> str:
        """Execute ``call`` and return the tool-result text the model reads next.

        Raises
        ------
        ValueError
            ``call.name`` is not one of the seven tools this dispatcher knows.
        """
        if call.name in _READ_TOOLS:
            return _to_json(_READ_TOOLS[call.name](self.tool_port, call))
        if call.name == "evaluate_dispute":
            return self._evaluate_dispute(call)
        if call.name == "create_dispute_case":
            return self._create_dispute_case(call, turn_id=turn_id)
        if call.name == "get_policy":
            return self._get_policy(call)
        if call.name == "handoff":
            return self._handoff(call, session_id=session_id, turn_id=turn_id, trace_id=trace_id)
        raise ValueError(f"unknown tool: {call.name!r}")

    def _evaluate_dispute(self, call: ToolCall) -> str:
        """Evaluate a transaction and category, recording an eligible decision as confirmable."""
        category = DisputeCategory(_str_arg(call, "category"))
        request = EvaluateDisputeRequest(
            transaction_ref=_str_arg(call, "transaction_ref"), category=category
        )
        result = dispatch_tool_port(self.tool_port, Tool.EVALUATE_DISPUTE, request)
        if isinstance(result, ToolFailure):
            return _to_json(result)
        self._decisions[(result.transaction_ref, category)] = result
        self._decisions_this_turn.append(result)
        # The decision a confirmation reply would be about: the turn's last eligible one, until a
        # filing is attempted (see ``_create_dispute_case``). ``evals.scoring`` grounds a
        # CONFIRM_FILING check in it for a system driven in process rather than over HTTP.
        self.last_confirmable_decision = result if result.outcome is Outcome.ELIGIBLE else None
        return _to_json(result)

    def _create_dispute_case(self, call: ToolCall, *, turn_id: str) -> str:
        """File a case with the decision this dispatcher recorded for the transaction and category.

        The tool port refuses the request when there is no recorded decision or the customer has
        not confirmed.
        """
        transaction_ref = _str_arg(call, "transaction_ref")
        category = DisputeCategory(_str_arg(call, "category"))
        decision = self._decisions.get((transaction_ref, category))
        request = CreateDisputeCaseRequest(
            transaction_ref=transaction_ref,
            category=category,
            confirmed=bool(call.input.get("confirmed", False)),
            idempotency_key=_idempotency_key(turn_id),
            decision=decision,
        )
        result = self.tool_port.create_dispute_case(request)
        # A filing attempt, successful or not, means the turn is no longer awaiting confirmation.
        self.last_confirmable_decision = None
        return _to_json(result)

    def _get_policy(self, call: ToolCall) -> str:
        """The corpus sections matching the query, as JSON of section id, title and body."""
        hits = self.retriever.search(_str_arg(call, "query"), self.lang)
        return json.dumps(
            [
                {
                    "section_id": hit.chunk.section_id,
                    "title": hit.chunk.title,
                    "body": hit.chunk.body,
                }
                for hit in hits
            ]
        )

    def _handoff(self, call: ToolCall, *, session_id: str, turn_id: str, trace_id: str) -> str:
        """Record a handoff packet for the named trigger and return its ticket reference.

        The reason codes are those of this turn's decisions that escalate; every other packet field
        is built here, not taken from the model.
        """
        trigger = HandoffTrigger(_str_arg(call, "trigger"))
        reason_codes: tuple[ReasonCode, ...] = tuple(
            decision.reason_code
            for decision in self._decisions_this_turn
            if decision.reason_code.value.startswith("escalate_")
        )
        content = HandoffContent(
            reference_date=self.calendar.reference_date,
            created_at=self.clock(),
            language=self.lang,
            trigger=trigger,
            first_name=_UNKNOWN_FIRST_NAME,
            customer_id=self.customer_id,
            request_summary=_REQUEST_SUMMARY_OF[trigger],
            reason_codes=reason_codes,
            policy_version=self.policy.version,
        )
        packet = self.outbox.record(
            content, session_id=session_id, turn_id=turn_id, trace_id=trace_id
        )
        self.handoff_ticket = packet.ticket_ref
        return json.dumps({"ticket_ref": packet.ticket_ref})


def _to_json(value: object) -> str:
    """A tool result as text the model can read: a pydantic model's own JSON, or a plain dict."""
    if hasattr(value, "model_dump_json"):
        return str(value.model_dump_json())
    return json.dumps(value)
