"""
B1 Conversation Loop
======================

Overview
--------
Drives one case's scripted turns against B1 (the naive agent), the same way
``evals.runner.proposed_system.run_case`` drives one against P, but over
``NaiveAgentClient`` and ``B1ToolDispatcher`` instead of an HTTP call — B1 never goes through
``/v1/turns`` (no session middleware, no ``Principal``, nothing to authenticate). This module
mints B1's own identifiers, runs the tool-call rounds each customer turn may need, and constructs
the same ``contracts.service_v1.api.TurnResponse`` shape P's real endpoint returns, so
``evals.scoring.score_case`` and ``evals.runner.runner.run_cases`` work unchanged across all three
system variants — one implementation of scoring, never a B1-specific one.

Scope
-----
In: ``build_b1_dependencies`` (B1's own client and tool dispatcher from ``Settings``, refused in
production); ``run_case``, driving one case's turns.
Out: the tool schemas and dispatch themselves (``evals.runner.baselines.b1_tools``, already
built); scoring a transcript (``evals.scoring``, unchanged); the ``make evaluate`` CLI wiring that
will call this for a batch (a following increment).

Design Principles
-----------------
- **B1 mints its own opaque identifiers, never a session token.** ``session_id`` has no foreign
  key to any sessions table anywhere in the schema (every store call takes it as a plain
  parameter), and minting one through the sandbox login would manufacture a real customer JWT for
  a variant that never goes through the session middleware at all. ``secrets.token_urlsafe(16)``
  is the same primitive ``SessionService.issue`` already uses for the same purpose.
- **Refused in production, the same way B0 is, for the same reason.** ``Settings.model_copy``
  and manual construction both skip the validation that would otherwise refuse this combination;
  ``build_b1_dependencies`` re-asserts ``app_env is not prod`` itself, before resolving anything
  else, matching ``evals.runner.baselines.b0.build_b0_app``'s own guard exactly.
- **One tool-call round trip at a time, capped.** The model may call tools any number of times
  before replying in text; a hard round cap (``_MAX_TOOL_ROUNDS``) stops a pathological loop from
  running forever against a real API budget — the evaluation plan's own token-budget-per-stage
  discipline, applied to the one system variant with no other bound on how many times it can call
  itself.
- **``next_expected`` is inferred from what happened this turn, not asked of the model.** A turn
  is tagged ``Slot.CONFIRMATION`` when its last tool call was an ``evaluate_dispute`` that returned
  an eligible decision and no ``create_dispute_case`` followed it in the same turn — mirroring the
  one structural signal ``evals.scoring``'s ``CONFIRM_FILING`` check already reads from P's own
  replies, so the same scorer reads the same signal from all three systems.

Runtime Contract
-----------------
``build_b1_dependencies(settings, *, policy, retriever, calendar, clock, customer_id, lang, model)
-> (NaiveAgentClient, B1ToolDispatcher, str)`` — the client, the dispatcher, and the session id it
minted. Raises ``ConfigError`` when ``settings.app_env`` is ``prod``.
``run_case(client, dispatcher, case, *, session_id, calendar) -> RunTranscript``.

Limitations
-----------
The model id B1 calls with is the caller's own choice, passed to ``build_b1_dependencies``
explicitly — the evaluation plan's "same model" wording does not say which of P's two configured
models (understanding vs. rendering) that means for a single unified agent role, and this module
does not decide it either; the caller (the CLI wiring, a following increment) names one from the
allow-list.
"""

from __future__ import annotations

# Standard libraries
import secrets

# Local modules
from app.config import AppEnvironment, ConfigError, Settings
from app.conversation.renderer import reference_date_line  # Pure; the same line P renders
from app.domain.calendar import DomainCalendar
from app.domain.policy.models import Policy
from app.persistence.audit import PostgresAuditSink
from app.persistence.handoff_outbox import PostgresHandoffOutbox
from app.persistence.reads import PostgresToolPort
from app.retrieval.lexical import Retriever
from app.security.sessions import Clock
from contracts.service_v1.api import TurnResponse
from contracts.service_v1.envelope import Lang, Slot
from evals.models import Case
from evals.runner.baselines.b1_tools import TOOL_SCHEMAS, B1ToolDispatcher
from evals.runner.baselines.naive_agent_client import NaiveAgentClient
from evals.scoring import RunTranscript

_MAX_TOOL_ROUNDS = 6
_MAX_TOKENS = 1024
_TIMEOUT_SECONDS = 30.0

_SYSTEM_PROMPT = (
    "You are a bank customer service assistant. A customer will describe a problem with a "
    "transaction on their account. Use the tools available to look up their transactions, "
    "check dispute eligibility, answer policy questions, file an eligible dispute only after "
    "the customer explicitly confirms, and hand off to a person for anything you cannot or "
    "should not resolve yourself (fraud, a lost card, a request to speak with a person, or a "
    "tool failure). Reply in the same language the customer writes in."
)


def build_b1_dependencies(
    settings: Settings,
    *,
    policy: Policy,
    retriever: Retriever,
    calendar: DomainCalendar,
    clock: Clock,
    customer_id: str,
    lang: Lang,
    model: str,
) -> tuple[NaiveAgentClient, B1ToolDispatcher, str]:
    """B1's own LLM client, tool dispatcher, and the session id it minted for this case.

    Raises
    ------
    ConfigError
        ``settings.app_env`` is ``prod``. B1 is an evaluation-only variant; it is refused in
        production for the same reason ``build_b0_app`` refuses B0 there.
    """
    if settings.app_env is AppEnvironment.PROD:
        raise ConfigError("the B1 baseline is not allowed when APP_ENV=prod")
    dsn = settings.require_database_url().get_secret_value()
    session_id = secrets.token_urlsafe(16)
    tool_port = PostgresToolPort(
        dsn,
        PostgresAuditSink(dsn),
        policy,
        customer_id=customer_id,
        session_id=session_id,
        trace_id=session_id,
        domain_date=calendar.reference_date,
        now=clock,
        language=lang,
        case_create_session_cap=settings.case_create_session_cap,
    )
    dispatcher = B1ToolDispatcher(
        tool_port=tool_port,
        retriever=retriever,
        outbox=PostgresHandoffOutbox(dsn),
        policy=policy,
        calendar=calendar,
        clock=clock,
        customer_id=customer_id,
        lang=lang,
    )
    client = NaiveAgentClient(settings.require_anthropic_key(), model=model)
    return client, dispatcher, session_id


def _run_turn(
    client: NaiveAgentClient,
    dispatcher: B1ToolDispatcher,
    messages: list[dict[str, object]],
    *,
    session_id: str,
    turn_id: str,
) -> tuple[str, bool]:
    """Run tool-call rounds for one customer turn until the model replies in text.

    Returns
    -------
    tuple[str, bool]
        The model's final text, and whether this turn's last ``evaluate_dispute`` call left an
        eligible decision with no ``create_dispute_case`` call after it (the ``next_expected``
        signal).
    """
    dispatcher.start_turn()
    reached_confirmable = False
    for _round in range(_MAX_TOOL_ROUNDS):
        turn = client.send(
            messages,
            TOOL_SCHEMAS,
            system=_SYSTEM_PROMPT,
            max_tokens=_MAX_TOKENS,
            timeout_seconds=_TIMEOUT_SECONDS,
        )
        if not turn.tool_calls:
            return turn.text, reached_confirmable
        content_blocks = [
            {"type": "tool_use", "id": call.id, "name": call.name, "input": dict(call.input)}
            for call in turn.tool_calls
        ]
        messages.append({"role": "assistant", "content": content_blocks})
        results = []
        reached_confirmable = False
        for call in turn.tool_calls:
            result_text = dispatcher.dispatch(
                call, session_id=session_id, turn_id=turn_id, trace_id=session_id
            )
            results.append({"type": "tool_result", "tool_use_id": call.id, "content": result_text})
            if call.name == "evaluate_dispute" and '"outcome":"eligible"' in result_text.replace(
                " ", ""
            ):
                reached_confirmable = True
            if call.name == "create_dispute_case":
                reached_confirmable = False
        messages.append({"role": "user", "content": results})
    return "", reached_confirmable


def run_case(
    client: NaiveAgentClient,
    dispatcher: B1ToolDispatcher,
    case: Case,
    *,
    session_id: str,
    calendar: DomainCalendar,
) -> RunTranscript:
    """Drive ``case``'s scripted turns against B1 and record the result."""
    messages: list[dict[str, object]] = []
    replies: list[TurnResponse] = []
    latencies: list[float] = []
    for index, text in enumerate(case.user_turns):
        messages.append({"role": "user", "content": text})
        turn_id = f"{case.case_id}-t{index:03d}"
        reply_text, reached_confirmable = _run_turn(
            client, dispatcher, messages, session_id=session_id, turn_id=turn_id
        )
        if reply_text:
            messages.append({"role": "assistant", "content": reply_text})
        replies.append(
            TurnResponse(
                turn_id=turn_id,
                conversation_id=session_id,
                state_version=index + 1,
                lang=case.lang,
                reply=reply_text or "(no reply)",
                reference_date_line=reference_date_line(calendar.reference_date, case.lang),
                next_expected=Slot.CONFIRMATION if reached_confirmable else None,
                end_session=(index == len(case.user_turns) - 1),
                handoff_ticket=dispatcher.handoff_ticket,
            )
        )
        latencies.append(0.0)
    return RunTranscript(
        case=case, session_id=session_id, replies=tuple(replies), latencies_seconds=tuple(latencies)
    )
