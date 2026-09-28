"""
Case Runner
============

Overview
--------
Ties the three pieces this slice has built so far into one pass over a set of cases: resolve each
case's customer (``evals.runner.seed_resolution``), drive its scripted turns against the running
system (``evals.runner.proposed_system``), and score the result (``evals.scoring``). This is the
proposed system's (P) own runner; a baseline variant reuses the same shape once it exists.

Scope
-----
In: ``run_cases``, sequencing the three already-built pieces over a batch.
Out: building any of the three pieces themselves; the 3-repeated-runs-for-P and
1-run-for-baselines protocol (``evals.golden``'s cases run once per call here; repetition is the
caller's own loop); a report generator; the judge; CI wiring (``make evaluate``, later increments).

Design Principles
-----------------
- **One pass, no hidden retry.** A case that fails to run (a non-2xx response, a malformed
  reference) raises and stops the batch rather than silently skipping it — the evaluation plan's
  own rule that "all results, including failures, land in the report" means a run that could not
  even execute is itself a fact worth surfacing loudly, not swallowing.
- **The same store connection resolves and scores; nothing is cached between the two.** Resolving
  a customer and scoring a run both read the real store fresh, each through its own already-built
  module, so a case's own data staying current between the two calls is never assumed.

Runtime Contract
-----------------
``run_cases(client, dsn, cases, *, test_login_key) -> tuple[CaseResult, ...]``, one result per
case, in the given order.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Sequence

# Third-party libraries
import httpx

# Local modules
from evals.metrics import CaseResult  # The verdict this module produces, one per case
from evals.models import Case  # The cases this module runs
from evals.runner.proposed_system import run_case  # Drives one case against the running system
from evals.runner.seed_resolution import resolve_customer_id  # Case.seed_ref -> customer id
from evals.scoring import score_case  # Turns a run into a verdict


def run_cases(
    client: httpx.Client, dsn: str, cases: Sequence[Case], *, test_login_key: str
) -> tuple[CaseResult, ...]:
    """Resolve, run and score every case in ``cases``, in order.

    Raises
    ------
    ValueError
        A case's ``seed_ref`` is malformed or names a transaction absent from the store.
    NotImplementedError
        A case's ``seed_ref`` names ``eval_bank``, or its ``expected_intent`` is not yet scored.
    httpx.HTTPStatusError
        The sandbox login or a turn call returned a non-2xx response for some case.
    """
    results = []
    for case in cases:
        customer_id = resolve_customer_id(dsn, case.seed_ref)
        transcript = run_case(client, case, customer_id=customer_id, test_login_key=test_login_key)
        results.append(score_case(dsn, transcript))
    return tuple(results)
