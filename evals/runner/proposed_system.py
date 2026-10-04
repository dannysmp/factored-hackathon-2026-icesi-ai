"""
Proposed System Adapter
=========================

Overview
--------
Drives one case's scripted turns against the running system exactly as a real client would: mint
a session through the sandbox login, then call the turns endpoint once per scripted line, in
order, over the same HTTP surface a customer's browser calls. This is system P — the full,
deployed architecture — as opposed to a baseline that reaches into the process directly.

Scope
-----
In: minting the session for one case's resolved customer, sending its scripted turns in order,
timing each call, and assembling the result into a ``RunTranscript``.
Out: resolving which customer a case's ``seed_ref`` names (``evals.runner.seed_resolution``);
scoring the transcript (``evals.scoring``); building the HTTP client or the app itself (the caller's
job — this module takes a ready client, so the same code drives a real deployed instance or an
in-process ASGI transport without caring which).

Design Principles
-----------------
- **The same HTTP surface a customer uses, nothing more.** This module never reaches past the
  turns endpoint into the process — no envelope, no internal state — for the same reason the
  scorer doesn't: crossing that boundary from outside would mean the runner is testing something
  a real client could never actually observe.
- **One deterministic, unique ``turn_id`` per call, derived from the case.** ``turn_id`` exists so a
  retried request never advances a conversation twice (the turns contract's guarantee); deriving it
  from the case id and the turn's position keeps every case's run reproducible and makes a replay of
  the same case produce the same request stream.
- **A non-2xx response is a runner failure, not a silently-absorbed one.** A case's scripted turn
  is expected to succeed at the HTTP level (a 4xx here means the harness itself is malformed, not
  that the case under test failed); ``httpx.Response.raise_for_status`` surfaces that immediately
  rather than letting a broken run score as if the conversation had actually happened.

Runtime Contract
-----------------
``run_case(client, case, *, customer_id, test_login_key) -> RunTranscript``.
"""

from __future__ import annotations

# Standard libraries
from time import perf_counter

# Third-party libraries
import httpx

# Local modules
from contracts.service_v1.api import TurnResponse  # One turn's parsed reply
from evals.models import Case  # The case being driven
from evals.scoring import RunTranscript  # The result this module assembles

TEST_SESSIONS_PATH = "/v1/auth/test-sessions"
TURNS_PATH = "/v1/turns"


def _mint_session(client: httpx.Client, *, customer_id: str, test_login_key: str) -> str:
    """A bearer token for ``customer_id``, through the sandbox login."""
    response = client.post(
        TEST_SESSIONS_PATH,
        json={"customer_id": customer_id},
        headers={"X-Test-Login-Key": test_login_key},
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def run_case(
    client: httpx.Client, case: Case, *, customer_id: str, test_login_key: str
) -> RunTranscript:
    """Drive ``case``'s scripted turns against ``client`` and record the result.

    Raises
    ------
    httpx.HTTPStatusError
        The sandbox login or a turn call returned a non-2xx response.
    """
    token = _mint_session(client, customer_id=customer_id, test_login_key=test_login_key)
    headers = {"Authorization": f"Bearer {token}"}

    replies: list[TurnResponse] = []
    latencies: list[float] = []
    for index, text in enumerate(case.user_turns):
        turn_id = f"{case.case_id}-t{index:03d}"
        started = perf_counter()
        response = client.post(TURNS_PATH, json={"turn_id": turn_id, "text": text}, headers=headers)
        latencies.append(perf_counter() - started)
        response.raise_for_status()
        replies.append(TurnResponse.model_validate(response.json()))

    return RunTranscript(
        case=case,
        session_id=replies[-1].conversation_id,
        replies=tuple(replies),
        latencies_seconds=tuple(latencies),
    )
