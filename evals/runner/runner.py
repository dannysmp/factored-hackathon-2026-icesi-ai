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
``run_cases(client, dsn, cases, *, test_login_key, capture_transcripts=False, cost_ledger=None) ->
tuple[CaseResult, ...]``, one result per case, in the given order — a normal verdict, or a named
error result for a case that could not resolve, run or be scored. ``capture_transcripts`` is the
opt-in step (default off, no behavior change for any existing caller) that additionally fills each
result's ``reply_text``/``facts_and_sources`` fields (``evals.facts.attach_masked_transcript``) for
a judge or a human rater to read later.

Limitations (capture)
----------------------
A capture failure (``CorpusIndexError``, ``KeyError`` — a declared policy section that does not
resolve) never demotes an otherwise-valid ``CaseResult`` to an error result: the case's real
verdict stands, only its two extra fields stay unset for that one case, logged so it is not silent.
This is deliberately narrower than ``_CASE_FAILURES``: those two exceptions still abort a run that
never opts into capture (``capture_transcripts=False``, every caller before this flag existed),
exactly as before.
"""

from __future__ import annotations

# Standard libraries
import dataclasses
import logging
from collections.abc import Sequence

# Third-party libraries
import httpx

# Local modules
from app.retrieval.corpus_index import CorpusIndexError  # A declared policy section not resolving
from evals.cost import TurnCostLedger  # Per-session model cost, read from the turn log
from evals.facts import attach_masked_transcript  # Fills reply_text/facts_and_sources, opt-in
from evals.metrics import CaseResult  # The verdict this module produces, one per case
from evals.models import Case  # The cases this module runs
from evals.runner.proposed_system import run_case  # Drives one case against the running system
from evals.runner.seed_resolution import resolve_customer_id  # Case.seed_ref -> customer id
from evals.scoring import error_result, score_case  # Turns a run (or a failure) into a verdict

logger = logging.getLogger(__name__)

#: Capture failure modes that leave a case's real verdict standing with its two extra fields
#: unset, rather than aborting the run — narrower than ``_CASE_FAILURES``, and only ever reached
#: when the caller opts into ``capture_transcripts``.
_CAPTURE_FAILURES: tuple[type[Exception], ...] = (CorpusIndexError, KeyError)

#: The batch's own documented, anticipated per-case failure modes — anything else still
#: propagates and stops the run (see the module's own Design Principles).
_CASE_FAILURES: tuple[type[Exception], ...] = (
    ValueError,
    NotImplementedError,
    httpx.HTTPStatusError,
)


def run_cases(
    client: httpx.Client,
    dsn: str,
    cases: Sequence[Case],
    *,
    test_login_key: str,
    capture_transcripts: bool = False,
    cost_ledger: TurnCostLedger | None = None,
) -> tuple[CaseResult, ...]:
    """Resolve, run and score every case in ``cases``, in order.

    Parameters
    ----------
    client : httpx.Client
        Drives each case's scripted turns against the running system.
    dsn : str
        Connection string ``resolve_customer_id`` and ``score_case`` each read fresh, per case.
    cases : Sequence[Case]
        The cases to run, in the order the returned results preserve.
    test_login_key : str
        Forwarded to the running system for every case's turns.
    capture_transcripts : bool
        Opt-in, default off: also fills each result's ``reply_text``/``facts_and_sources`` fields
        for a judge or a human rater to read later (see the module's own Limitations (capture)).
    cost_ledger : TurnCostLedger | None
        When given, each case's result carries the model cost the ledger recorded for the case's
        session (``None`` where it recorded none); when omitted, no cost is attached.

    Returns
    -------
    tuple[CaseResult, ...]
        One result per case, in the given order — a normal verdict, or a named error result for a
        case that could not resolve, run or be scored.

    Raises
    ------
    Exception
        Any exception other than ``ValueError``, ``NotImplementedError`` or
        ``httpx.HTTPStatusError`` propagates unchanged and stops the batch immediately, without
        attempting the cases still queued behind it; those three are this module's own
        documented, anticipated failure modes and are recorded as a named ``CaseResult.error``
        instead (see the module's own Design Principles). A capture failure never propagates this
        far regardless (see Limitations (capture)).
    """
    results = []
    for case in cases:
        try:
            customer_id = resolve_customer_id(dsn, case.seed_ref)
            transcript = run_case(
                client, case, customer_id=customer_id, test_login_key=test_login_key
            )
            if cost_ledger is not None:
                transcript = dataclasses.replace(
                    transcript, cost_usd=cost_ledger.cost_for(transcript.session_id)
                )
            result = score_case(dsn, transcript)
        except _CASE_FAILURES as exc:
            results.append(error_result(case, exc))
            continue
        if capture_transcripts:
            try:
                result = attach_masked_transcript(dsn, transcript, result)
            except _CAPTURE_FAILURES as exc:
                logger.warning("transcript_capture_failed case_id=%s error=%s", case.case_id, exc)
        results.append(result)
    return tuple(results)
