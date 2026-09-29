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
- **One case's failure never silences the rest of the batch.** A case that fails to resolve, run
  or score (a malformed reference, a non-2xx response, an intent this module's scorer has not
  been taught to read) is recorded as a named ``CaseResult.error`` (``evals.scoring.error_result``)
  and the batch continues — the evaluation plan's own rule that "all results, including failures,
  land in the report" means one case's failure is itself a fact worth recording, not one that
  should hide every other case's own result behind it. A batch this size, run against a real
  system and a real model, cannot afford one bad case aborting the other 134.
- **Only the three failure classes this module already names are ever caught.** ``ValueError``
  (a malformed or unresolvable ``seed_ref``), ``httpx.HTTPStatusError`` (a non-2xx turn response)
  and ``NotImplementedError`` (an ``expected_intent`` the scorer does not read) are the batch's
  own documented, anticipated failure modes; anything else (a programming error, a connection
  refused before any HTTP exchange happens) still propagates and stops the run, since silently
  swallowing an unanticipated exception would hide a real bug behind a misleading "case failed"
  record.
- **The same store connection resolves and scores; nothing is cached between the two.** Resolving
  a customer and scoring a run both read the real store fresh, each through its own already-built
  module, so a case's own data staying current between the two calls is never assumed.

Runtime Contract
-----------------
``run_cases(client, dsn, cases, *, test_login_key) -> tuple[CaseResult, ...]``, one result per
case, in the given order — a normal verdict, or a named error result for a case that could not
resolve, run or be scored.
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
from evals.scoring import error_result, score_case  # Turns a run (or a failure) into a verdict

#: The batch's own documented, anticipated per-case failure modes — anything else still
#: propagates and stops the run (see the module's own Design Principles).
_CASE_FAILURES: tuple[type[Exception], ...] = (
    ValueError,
    NotImplementedError,
    httpx.HTTPStatusError,
)


def run_cases(
    client: httpx.Client, dsn: str, cases: Sequence[Case], *, test_login_key: str
) -> tuple[CaseResult, ...]:
    """Resolve, run and score every case in ``cases``, in order.

    A case that fails to resolve, run or score with ``ValueError``, ``NotImplementedError`` or
    ``httpx.HTTPStatusError`` (a malformed ``seed_ref``, an unscored ``expected_intent``, a
    non-2xx turn response) is recorded as a named ``CaseResult.error`` instead of stopping the
    batch; any other exception still propagates and stops it.
    """
    results = []
    for case in cases:
        try:
            customer_id = resolve_customer_id(dsn, case.seed_ref)
            transcript = run_case(
                client, case, customer_id=customer_id, test_login_key=test_login_key
            )
            results.append(score_case(dsn, transcript))
        except _CASE_FAILURES as exc:
            results.append(error_result(case, exc))
    return tuple(results)
