"""
Proposed System Adapter
=======================

Overview
--------
Drives one case's scripted turns against the running system as a real client would: mint a session
through the sandbox login, then call the turns endpoint once per scripted line, in order, over the
HTTP surface a customer's browser uses. This is system P, the full deployed architecture, as
opposed to a baseline that reaches into the process directly.

Scope
-----
In: minting the session for one case's resolved customer, sending its scripted turns in order,
timing each call, and assembling the result into a ``RunTranscript``.
Out: resolving which customer a case's ``seed_ref`` names (``evals.runner.seed_resolution``);
scoring the transcript (``evals.scoring``); building the HTTP client or the application, which the
caller does. The module takes a ready client, so the same code drives a deployed instance or an
in-process ASGI transport.

Design Principles
-----------------
- **Only the HTTP surface a customer uses.** The module never reaches past the turns endpoint into
  the process (no envelope, no internal state), so the run observes only what a real client could.
- **One deterministic, unique ``turn_id`` per call, derived from the case.** ``turn_id`` exists so
  a retried request never advances a conversation twice (the turns contract's guarantee); deriving
  it from the case id and the turn's position makes a case's run reproducible and a replay send
  the same request stream.
- **A non-2xx response is a runner failure, not an absorbed one.** A scripted turn is expected to
  succeed at the HTTP level (a 4xx means the harness itself is malformed, not that the case under
  test failed); ``httpx.Response.raise_for_status`` surfaces it immediately rather than letting a
  broken run score as if the conversation had happened.

Runtime Contract
----------------
``run_case(client, case, *, customer_id, test_login_key) -> RunTranscript``. The transcript's
``session_id`` is the conversation id of the last reply, and its latencies are one wall-clock
duration per turn call.

Limitations
-----------
Latency covers only the turns call (request sent to response received), not session minting, and
includes whatever model calls the system made during that turn.
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

#: The sandbox login that mints a session for a given customer.
TEST_SESSIONS_PATH = "/v1/auth/test-sessions"
#: The endpoint each scripted turn is posted to.
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
