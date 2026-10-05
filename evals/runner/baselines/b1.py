"""
B1 Conversation Loop
====================

Overview
--------
Drives one case's scripted turns against B1 (the naive agent) as ``evals.runner.proposed_system``
drives them against P, but over ``NaiveAgentClient`` and ``B1ToolDispatcher`` rather than HTTP: B1
never goes through ``/v1/turns``, so there is no session middleware, no ``Principal`` and nothing
to authenticate. The module mints B1's own identifiers, runs the tool-call rounds each customer turn
may need, and builds the ``contracts.service_v1.api.TurnResponse`` shape P's endpoint returns, so
``evals.scoring.score_case`` scores all three system variants with one implementation.

Scope
-----
In: ``build_b1_dependencies`` (B1's client and tool dispatcher from ``Settings``, refused in
production); ``run_case``, driving one case's turns; ``run_cases``, sequencing that over a batch as
``evals.runner.runner.run_cases`` does for P and B0.
Out: the tool schemas and dispatch (``evals.runner.baselines.b1_tools``); scoring a transcript
(``evals.scoring``); the command-line wiring that calls ``run_cases`` (``evals.cli``).

Design Principles
-----------------
- **B1 mints opaque identifiers, never a session token.** ``session_id`` is passed to the store as
  a plain parameter, and minting one through the sandbox login would create a real customer JWT
  for a variant that never reaches the session middleware. ``secrets.token_urlsafe(16)`` is the
  primitive ``SessionService.issue`` uses for the same purpose.
- **Refused in production, as B0 is, for the same reason.** ``Settings.model_copy`` and manual
  construction skip the validation that would refuse this combination, so ``build_b1_dependencies``
  and ``run_cases`` check ``app_env`` themselves, as ``evals.runner.baselines.b0.build_b0_app``
  does.
- **One tool-call round trip at a time, capped.** The model may call tools repeatedly before it
  replies in text; a hard round cap (``_MAX_TOOL_ROUNDS``) stops a pathological loop from spending
  a real API budget, since B1 has no other bound on how often it calls itself.
- **``next_expected`` is inferred from what happened in the turn, not asked of the model.** A turn
  is tagged ``Slot.CONFIRMATION`` when its last ``evaluate_dispute`` call returned an eligible
  decision and no ``create_dispute_case`` followed it in the same turn, whatever other tools were
  called in between. This is the structural signal ``evals.scoring``'s ``CONFIRM_FILING`` check
  reads from P's replies, so one scorer reads the same signal from all three systems.
- **B1's grounded decision travels with the transcript.** Over HTTP, P and B0 are a black box, so
  ``evals.scoring.score_case`` reads ``dialogue_state`` after the run to learn which transaction
  and category a ``CONFIRM_FILING`` reply confirmed. B1 has no such table, but the harness holds
  the tool dispatcher in process (``B1ToolDispatcher.last_confirmable_decision``), so ``run_case``
  carries that decision as ``RunTranscript.confirmed_target``: the same question answered from what
  this transport exposes, and no looser a check than for P or B0.
- **One case's failure never silences the rest of the batch.** As in
  ``evals.runner.runner.run_cases``, a case that fails with ``ValueError``,
  ``NotImplementedError``, ``LlmUnavailable`` or ``NaiveAgentRequestTooLarge`` is recorded as a
  named ``CaseResult.error`` (``evals.scoring.error_result``) and the run continues; any other
  exception propagates. ``LlmUnavailable`` is B1's addition to the HTTP runner's set: a real
  provider call can time out, hit a rate limit or answer with a 5xx independently of the case, and
  P and B0 see the same failure as an ``httpx.HTTPStatusError``.

Runtime Contract
----------------
``build_b1_dependencies(settings, *, policy, retriever, calendar, clock, customer_id, lang, model)
-> (NaiveAgentClient, B1ToolDispatcher, str)``: the client, the dispatcher and the session id it
minted. Raises ``ConfigError`` when ``settings.app_env`` is ``prod``.
``run_case(client, dispatcher, case, *, session_id, calendar) -> RunTranscript``.
``run_cases(client, settings, dsn, cases, *, policy, retriever, calendar, clock)
-> tuple[CaseResult, ...]``: reuses the caller's ``client`` for every case, and for each case in
order resolves the customer, builds a fresh dispatcher and session id, drives the turns and scores
the result. A case's ``injected_failure`` fails that tool for that case's dispatcher only.

Limitations
-----------
The model id B1 calls with is the caller's choice, passed to ``build_b1_dependencies``. B1 is
meant to use the same model as P, but P has two configured models (understanding and rendering) and
a single unified agent role matches neither exactly; this module does not decide, and the caller
(``evals.cli``) names one. ``run_cases`` records a ``NaiveAgentRequestTooLarge`` (a conversation
that outgrew the provider's request-byte limit) against its case and continues, but propagates any
other ``LlmRequestRejected``: bad credentials or missing model access are account-level, recur for
every case, and are better surfaced once by stopping the run.
"""

from __future__ import annotations

# Standard libraries
import logging
import secrets
from collections.abc import Sequence
from decimal import Decimal

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
from evals.injector import FailureInjectingToolPort
from evals.metrics import CaseResult
from evals.models import Case, InjectedToolFailure
from evals.runner.baselines.b1_tools import TOOL_SCHEMAS, B1ToolDispatcher
from evals.runner.baselines.naive_agent_client import (
    NaiveAgentClient,
    NaiveAgentRequestTooLarge,
    NaiveAgentTurn,
)
from evals.runner.seed_resolution import resolve_customer_id
from evals.scoring import RunTranscript, error_result, score_case

logger = logging.getLogger(__name__)

# Most model calls one customer turn may make before B1 is cut off with an empty reply.
_MAX_TOOL_ROUNDS = 6
# Per-call limits on the model reply length and on the wait for the provider.
_MAX_TOKENS = 1024
_TIMEOUT_SECONDS = 30.0

#: The anticipated per-case failure modes, each recorded as an error result; anything else
#: propagates and stops the run, as in ``evals.runner.runner``.
#: ``LlmUnavailable`` (a timeout, a rate limit or a 5xx from the provider) is B1's addition: P and
#: B0 see the same failure as an ``httpx.HTTPStatusError``, while B1 calls the provider directly.
#: ``LlmRequestRejected`` is deliberately excluded: its usual causes (bad credentials, no model
#: access) are account-level and recur for every case, so stopping the run surfaces them once
#: rather than recording the same failure for every case. ``NaiveAgentRequestTooLarge`` is the one
#: rejection driven by a single case's conversation (a 413), so it is recorded per case.
_CASE_FAILURES: tuple[type[Exception], ...] = (
    ValueError,
    NotImplementedError,
    LlmUnavailable,
    NaiveAgentRequestTooLarge,
)

# B1's only instruction: a plain task description, with no policy, flow or safety rules, because
# B1 measures what an unsupervised model does with the tools alone.
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
    injected_failure: InjectedToolFailure | None = None,
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
        tool_port=FailureInjectingToolPort(tool_port, injected_failure),
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


def _log_call_completed(session_id: str, turn_id: str, turn: NaiveAgentTurn) -> Decimal | None:
    """Log one B1 model call and return its cost; ``None`` when its model is unpriced.

    The line mirrors ``app.conversation.controller.DialogueController._log_turn_completed``, and an
    unpriced model never aborts the run. It is the only place B1's spend is recorded (the token
    counts of ``NaiveAgentTurn`` are discarded afterwards), so a run's cost can be computed from
    logs alone, as for P. The returned cost is the figure the line carries, so the harness sums the
    same number.
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
    return cost


def _run_turn(
    client: NaiveAgentClient,
    dispatcher: B1ToolDispatcher,
    messages: list[dict[str, object]],
    *,
    session_id: str,
    turn_id: str,
) -> tuple[str, bool, float, Decimal | None]:
    """Run tool-call rounds for one customer turn until the model replies in text.

    Returns
    -------
    tuple[str, bool, float, Decimal | None]
        The model's final text; whether this turn's last ``evaluate_dispute`` call left an
        eligible decision with no ``create_dispute_case`` call after it (the ``next_expected``
        signal); and the turn's total latency in seconds, summed over every ``NaiveAgentClient``
        call this turn made (a turn with several tool-call rounds makes several calls); and those
        calls' summed cost, ``None`` when any of them was to an unpriced model.
    """
    dispatcher.start_turn()
    reached_confirmable = False
    latency_seconds = 0.0
    turn_cost: Decimal | None = Decimal(0)
    for _round in range(_MAX_TOOL_ROUNDS):
        turn = client.send(
            messages,
            TOOL_SCHEMAS,
            system=_SYSTEM_PROMPT,
            max_tokens=_MAX_TOKENS,
            timeout_seconds=_TIMEOUT_SECONDS,
        )
        call_cost = _log_call_completed(session_id, turn_id, turn)
        turn_cost = None if turn_cost is None or call_cost is None else turn_cost + call_cost
        latency_seconds += turn.latency_ms / 1000
        if not turn.tool_calls:
            return turn.text, reached_confirmable, latency_seconds, turn_cost
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
    return "", reached_confirmable, latency_seconds, turn_cost


def run_case(
    client: NaiveAgentClient,
    dispatcher: B1ToolDispatcher,
    case: Case,
    *,
    session_id: str,
    calendar: DomainCalendar,
) -> RunTranscript:
    """Drive ``case``'s scripted turns against B1 and record the result.

    The transcript's ``cost_usd`` is the summed model cost of every call, ``None`` when any call
    was to an unpriced model; ``handoff_ticket`` on each reply is the turn's handoff reference.
    A turn that ends without text yields the reply ``(no reply)``.
    """
    messages: list[dict[str, object]] = []
    replies: list[TurnResponse] = []
    latencies: list[float] = []
    case_cost: Decimal | None = Decimal(0)
    for index, text in enumerate(case.user_turns):
        messages.append({"role": "user", "content": text})
        turn_id = f"{case.case_id}-t{index:03d}"
        reply_text, reached_confirmable, latency_seconds, turn_cost = _run_turn(
            client, dispatcher, messages, session_id=session_id, turn_id=turn_id
        )
        case_cost = None if case_cost is None or turn_cost is None else case_cost + turn_cost
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
        cost_usd=None if case_cost is None else float(case_cost),
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

    ``client`` is built once by the caller and reused for every case, as
    ``evals.runner.runner.run_cases`` reuses its ``httpx.Client``. Only the tool dispatcher and
    session id are rebuilt per case, because a ``B1ToolDispatcher`` is scoped to one customer and
    language and each case's ``seed_ref`` and ``lang`` may differ.

    A case that fails to resolve, run or score with ``ValueError``, ``NotImplementedError``,
    ``LlmUnavailable`` or ``NaiveAgentRequestTooLarge`` (a malformed ``seed_ref``, an unscored
    ``expected_intent``, a transient failure from the real Anthropic API, a conversation too large
    for one request) is recorded as a named ``CaseResult.error`` instead of stopping the batch;
    any other exception still propagates, including an account-level ``LlmRequestRejected`` such
    as bad credentials.

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
                injected_failure=case.injected_failure,
            )
            transcript = run_case(
                client, dispatcher, case, session_id=session_id, calendar=calendar
            )
            results.append(score_case(dsn, transcript))
        except _CASE_FAILURES as exc:
            results.append(error_result(case, exc))
    return tuple(results)
