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
production); ``run_case``, driving one case's turns; ``run_cases``, sequencing that over a batch,
the same shape ``evals.runner.runner.run_cases`` already gives P and B0.
Out: the tool schemas and dispatch themselves (``evals.runner.baselines.b1_tools``, already
built); scoring a transcript (``evals.scoring``, unchanged); the ``make evaluate`` CLI wiring that
will call ``run_cases`` for P, B0 or B1 alike (a following increment).

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
  is tagged ``Slot.CONFIRMATION`` when this turn's last ``evaluate_dispute`` call returned an
  eligible decision and no ``create_dispute_case`` followed it in the same turn — regardless of
  what other tools, if any, the model called in between — mirroring the one structural signal
  ``evals.scoring``'s ``CONFIRM_FILING`` check already reads from P's own replies, so the same
  scorer reads the same signal from all three systems.
- **B1's own grounded decision travels with the transcript, since the harness is B1's caller.**
  P and B0 are a black box to the harness over HTTP, so ``evals.scoring.score_case`` reads
  ``dialogue_state`` back after the run to check which transaction and category a
  ``CONFIRM_FILING`` reply actually confirmed. B1 has no such table to read: the harness already
  holds the tool port's own grounded decision in-process
  (``B1ToolDispatcher.last_confirmable_decision``), so ``run_case`` carries it through
  ``RunTranscript.confirmed_target`` instead — the same question, answered from the vantage point
  this transport actually exposes, never a looser check than P's or B0's own.
- **One case's failure never silences the rest of the batch**, the same rule
  ``evals.runner.runner.run_cases`` applies: a case that fails to resolve or score with
  ``ValueError``, ``NotImplementedError`` or ``LlmUnavailable`` is recorded as a named
  ``CaseResult.error`` (``evals.scoring.error_result``) instead of stopping the run; any other
  exception still propagates. ``LlmUnavailable`` is B1's own addition to the two failure classes
  the HTTP runner already anticipates: a real Anthropic API call can time out, hit a rate limit or
  answer with a 5xx independently of anything about the case itself, and one such transient blip
  must not cost the batch every case still queued behind it — the same reasoning that already
  puts ``httpx.HTTPStatusError`` (P and B0's own equivalent, surfaced through the turns endpoint)
  on the HTTP runner's list.

Runtime Contract
-----------------
``build_b1_dependencies(settings, *, policy, retriever, calendar, clock, customer_id, lang, model)
-> (NaiveAgentClient, B1ToolDispatcher, str)`` — the client, the dispatcher, and the session id it
minted. Raises ``ConfigError`` when ``settings.app_env`` is ``prod``.
``run_case(client, dispatcher, case, *, session_id, calendar) -> RunTranscript``.
``run_cases(client, settings, dsn, cases, *, policy, retriever, calendar, clock)
-> tuple[CaseResult, ...]`` — the caller's own ``client``, reused for every case; resolves,
builds a fresh dispatcher and session id for, drives and scores each case in order.

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
import logging
import secrets
from collections.abc import Sequence

# Local modules
from app.config import AppEnvironment, ConfigError, Settings
from app.conversation.renderer import reference_date_line  # Pure; the same line P renders
from app.domain.calendar import DomainCalendar
from app.domain.policy.models import Policy
from app.llm.client import LlmUnavailable  # A transient provider failure, safe to retry later
from app.llm.pricing import cost_usd  # Per-call cost accounting, the same table P's own turns use
from app.persistence.audit import PostgresAuditSink
from app.persistence.handoff_outbox import PostgresHandoffOutbox
from app.persistence.reads import PostgresToolPort
from app.retrieval.lexical import Retriever
from app.security.sessions import Clock
from contracts.service_v1.api import TurnResponse
from contracts.service_v1.envelope import Lang, Slot
from evals.metrics import CaseResult
from evals.models import Case
from evals.runner.baselines.b1_tools import TOOL_SCHEMAS, B1ToolDispatcher
from evals.runner.baselines.naive_agent_client import NaiveAgentClient, NaiveAgentTurn
from evals.runner.seed_resolution import resolve_customer_id
from evals.scoring import RunTranscript, error_result, score_case

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 6
_MAX_TOKENS = 1024
_TIMEOUT_SECONDS = 30.0

#: The batch's own documented, anticipated per-case failure modes — anything else still
#: propagates and stops the run, the same rule ``evals.runner.runner.run_cases`` applies.
#: ``LlmUnavailable`` (a timeout, a rate limit, a 5xx from the real Anthropic API) is B1's own
#: addition to the set the HTTP runner already catches: P and B0 surface the same class of
#: provider failure as ``httpx.HTTPStatusError`` through the turns endpoint, already anticipated
#: there; B1 calls the provider directly, so it needs the same failure named in its own terms.
#: ``LlmRequestRejected`` (bad credentials, no model access) is deliberately not included here —
#: an account-level problem recurs identically for every case in the batch, so stopping the run
#: outright surfaces it once, loudly, rather than recording the same failure 135 times over.
_CASE_FAILURES: tuple[type[Exception], ...] = (ValueError, NotImplementedError, LlmUnavailable)

_SYSTEM_PROMPT = (
    "You are a bank customer service assistant. A customer will describe a problem with a "
    "transaction on their account. Use the tools available to look up their transactions, "
    "check dispute eligibility, answer policy questions, file an eligible dispute only after "
    "the customer explicitly confirms, and hand off to a person for anything you cannot or "
    "should not resolve yourself (fraud, a lost card, a request to speak with a person, or a "
    "tool failure). Reply in the same language the customer writes in."
)


def _build_dispatcher(
    settings: Settings,
    *,
    policy: Policy,
    retriever: Retriever,
    calendar: DomainCalendar,
    clock: Clock,
    customer_id: str,
    lang: Lang,
) -> tuple[B1ToolDispatcher, str]:
    """A fresh tool dispatcher and the opaque session id minted for it, scoped to one customer.

    No production guard here: the caller (``build_b1_dependencies`` or ``run_cases``) checks
    ``settings.app_env`` itself, once, before calling this for one case or a whole batch.
    """
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
    return dispatcher, session_id


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
    dispatcher, session_id = _build_dispatcher(
        settings,
        policy=policy,
        retriever=retriever,
        calendar=calendar,
        clock=clock,
        customer_id=customer_id,
        lang=lang,
    )
    client = NaiveAgentClient(settings.require_anthropic_key(), model=model)
    return client, dispatcher, session_id


def _log_call_completed(session_id: str, turn_id: str, turn: NaiveAgentTurn) -> None:
    """One stable-shaped log line per real B1 call, mirroring
    ``app.conversation.controller.DialogueController._log_turn_completed``'s own shape and its
    "an unpriced model never aborts the run" rule: this is the only place B1's own spend is
    recorded anywhere (``NaiveAgentTurn``'s token counts are otherwise discarded once this
    function returns), so a real evaluation run's cost is computable from logs alone, the same
    guarantee E9 already established for P.
    """
    try:
        cost = cost_usd(turn.model, turn.input_tokens, turn.output_tokens)
    except KeyError:
        logger.warning("b1_call_cost_unpriced model=%s", turn.model)
        cost = None
    logger.info(
        "b1_call_completed session_id=%s turn_id=%s model=%s input_tokens=%s output_tokens=%s "
        "latency_ms=%s cost_usd=%s",
        session_id,
        turn_id,
        turn.model,
        turn.input_tokens,
        turn.output_tokens,
        turn.latency_ms,
        cost,
    )


def _run_turn(
    client: NaiveAgentClient,
    dispatcher: B1ToolDispatcher,
    messages: list[dict[str, object]],
    *,
    session_id: str,
    turn_id: str,
) -> tuple[str, bool, float]:
    """Run tool-call rounds for one customer turn until the model replies in text.

    Returns
    -------
    tuple[str, bool, float]
        The model's final text; whether this turn's last ``evaluate_dispute`` call left an
        eligible decision with no ``create_dispute_case`` call after it (the ``next_expected``
        signal); and the turn's total latency in seconds, summed over every ``NaiveAgentClient``
        call this turn made (a turn with several tool-call rounds makes several calls).
    """
    dispatcher.start_turn()
    reached_confirmable = False
    latency_seconds = 0.0
    for _round in range(_MAX_TOOL_ROUNDS):
        turn = client.send(
            messages,
            TOOL_SCHEMAS,
            system=_SYSTEM_PROMPT,
            max_tokens=_MAX_TOKENS,
            timeout_seconds=_TIMEOUT_SECONDS,
        )
        _log_call_completed(session_id, turn_id, turn)
        latency_seconds += turn.latency_ms / 1000
        if not turn.tool_calls:
            return turn.text, reached_confirmable, latency_seconds
        content_blocks = [
            {"type": "tool_use", "id": call.id, "name": call.name, "input": dict(call.input)}
            for call in turn.tool_calls
        ]
        messages.append({"role": "assistant", "content": content_blocks})
        results = []
        for call in turn.tool_calls:
            result_text = dispatcher.dispatch(
                call, session_id=session_id, turn_id=turn_id, trace_id=session_id
            )
            results.append({"type": "tool_result", "tool_use_id": call.id, "content": result_text})
            if call.name == "evaluate_dispute":
                reached_confirmable = '"outcome":"eligible"' in result_text.replace(" ", "")
            elif call.name == "create_dispute_case":
                reached_confirmable = False
        messages.append({"role": "user", "content": results})
    return "", reached_confirmable, latency_seconds


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
        reply_text, reached_confirmable, latency_seconds = _run_turn(
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
        latencies.append(latency_seconds)
    decision = dispatcher.last_confirmable_decision
    confirmed_target = (decision.transaction_ref, decision.category) if decision else None
    return RunTranscript(
        case=case,
        session_id=session_id,
        replies=tuple(replies),
        latencies_seconds=tuple(latencies),
        confirmed_target=confirmed_target,
    )


def run_cases(
    client: NaiveAgentClient,
    settings: Settings,
    dsn: str,
    cases: Sequence[Case],
    *,
    policy: Policy,
    retriever: Retriever,
    calendar: DomainCalendar,
    clock: Clock,
) -> tuple[CaseResult, ...]:
    """Resolve, run and score every case in ``cases`` against B1, in order.

    ``client`` is built once by the caller and reused for every case — the same shape
    ``evals.runner.runner.run_cases`` takes an ``httpx.Client`` P reuses across its own batch —
    so a test can inject a stub the same way it already does for one case with ``run_case``. Only
    the tool dispatcher and session id are rebuilt per case: B1ToolDispatcher is scoped to one
    customer and language, and a case's own seed_ref and lang may each differ from the last case's.

    A case that fails to resolve, run or score with ``ValueError``, ``NotImplementedError`` or
    ``LlmUnavailable`` (a malformed ``seed_ref``, an unscored ``expected_intent``, a transient
    failure from the real Anthropic API) is recorded as a named ``CaseResult.error`` instead of
    stopping the batch; any other exception still propagates.

    Raises
    ------
    ConfigError
        ``settings.app_env`` is ``prod``.
    """
    if settings.app_env is AppEnvironment.PROD:
        raise ConfigError("the B1 baseline is not allowed when APP_ENV=prod")
    results = []
    for case in cases:
        try:
            customer_id = resolve_customer_id(dsn, case.seed_ref)
            dispatcher, session_id = _build_dispatcher(
                settings,
                policy=policy,
                retriever=retriever,
                calendar=calendar,
                clock=clock,
                customer_id=customer_id,
                lang=case.lang,
            )
            transcript = run_case(
                client, dispatcher, case, session_id=session_id, calendar=calendar
            )
            results.append(score_case(dsn, transcript))
        except _CASE_FAILURES as exc:
            results.append(error_result(case, exc))
    return tuple(results)
