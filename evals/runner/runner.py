"""
Case Runner
===========

Overview
--------
Ties three pieces into one pass over a set of cases: resolve each case's customer
(``evals.runner.seed_resolution``), drive its scripted turns against the running system
(``evals.runner.proposed_system``), and score the result (``evals.scoring``). It serves the
proposed system (P) and baseline B0, which are both driven over the HTTP surface.

Scope
-----
In: ``run_cases``, sequencing the three pieces over a batch.
Out: building any of the three pieces; repeating a run (each call runs every case once, and the
caller loops for P's repeated runs); report generation; the judge; command-line wiring
(``evals.cli``).

Design Principles
-----------------
- **One case's failure never silences the rest of the batch.** A case that fails to resolve, run
  or score (a malformed reference, a non-2xx response, an intent the scorer does not read) is
  recorded as a named ``CaseResult.error`` (``evals.scoring.error_result``) and the batch
  continues. The failure is itself a result worth reporting, and a long batch against a real
  system and model cannot afford one bad case aborting the rest.
- **Only the three anticipated failure classes are caught.** ``ValueError`` (a malformed or
  unresolvable ``seed_ref``), ``httpx.HTTPStatusError`` (a non-2xx turn response) and
  ``NotImplementedError`` (an ``expected_intent`` the scorer does not read) are the documented
  per-case failures. Anything else (a programming error, a connection refused before any HTTP
  exchange) propagates and stops the run, since swallowing it would hide a real bug behind a
  misleading "case failed" record.
- **Resolution and scoring each read the store fresh.** Neither caches anything between the two
  calls, so a case's data is never assumed to be unchanged between them.

Runtime Contract
----------------
``run_cases(client, dsn, cases, *, test_login_key, capture_transcripts=False, cost_ledger=None,
failure_schedule=None) -> tuple[CaseResult, ...]``, one result per case in the given order: a
normal verdict, or a named error result for a case that could not resolve, run or be scored.
``capture_transcripts`` (off by default) additionally fills each result's ``reply_text`` and
``facts_and_sources`` (``evals.facts.attach_masked_transcript``) for a judge or a human rater to
read. ``failure_schedule`` receives each case's injected failure before the case runs.

Limitations
-----------
A capture failure (``CorpusIndexError`` or ``KeyError``, for example a declared policy section that
does not resolve) never demotes an otherwise valid ``CaseResult`` to an error result: the case's
verdict stands, its two extra fields stay unset, and the failure is logged. The two exception
sets are separate: ``_CASE_FAILURES`` (``ValueError``, ``NotImplementedError`` and
``httpx.HTTPStatusError``) turns a case into a named error result, while the capture step handles
only ``CorpusIndexError`` and ``KeyError``. Anything else raised there propagates and stops the
run. A case without captured fields is therefore absent from anything that needs a transcript,
such as the live judge's scores.
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
from evals.injector import FailureSchedule  # The failure a case declares, read by the system
from evals.metrics import CaseResult  # The verdict this module produces, one per case
from evals.models import Case  # The cases this module runs
from evals.runner.proposed_system import run_case  # Drives one case against the running system
from evals.runner.seed_resolution import resolve_customer_id  # Case.seed_ref -> customer id
from evals.scoring import error_result, score_case  # Turns a run (or a failure) into a verdict

logger = logging.getLogger(__name__)

#: Capture failure modes that leave a case's verdict standing with its two extra fields unset
#: instead of aborting the run; reached only when the caller opts into ``capture_transcripts``.
_CAPTURE_FAILURES: tuple[type[Exception], ...] = (CorpusIndexError, KeyError)

#: The anticipated per-case failure modes, each recorded as an error result; anything else
#: propagates and stops the run.
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
    failure_schedule: FailureSchedule | None = None,
) -> tuple[CaseResult, ...]:
    """Resolve, run and score every case in ``cases``, in order.

    Parameters
    ----------
    client : httpx.Client
        Drives each case's scripted turns against the running system.
    dsn : str
        Connection string ``resolve_customer_id`` and ``score_case`` each open per case.
    cases : Sequence[Case]
        The cases to run, in the order the returned results preserve.
    test_login_key : str
        Forwarded to the running system for every case's turns.
    capture_transcripts : bool
        Opt-in, default off: also fills each result's ``reply_text``/``facts_and_sources`` fields
        for a judge or a human rater to read later (see this module's Limitations).
    cost_ledger : TurnCostLedger | None
        When given, each case's result carries the model cost the ledger recorded for the case's
        session (``None`` where it recorded none); when omitted, no cost is attached.
    failure_schedule : FailureSchedule | None
        The schedule the running system's tool ports read. Before each case it is set to the
        case's own ``injected_failure`` (``None`` for a case that declares none) and cleared when
        the batch ends; when omitted, no failure is ever injected.

    Returns
    -------
    tuple[CaseResult, ...]
        One result per case, in the given order — a normal verdict, or a named error result for a
        case that could not resolve, run or be scored.

    Raises
    ------
    Exception
        Any exception other than ``ValueError``, ``NotImplementedError`` or
        ``httpx.HTTPStatusError`` propagates unchanged and stops the batch, leaving the remaining
        cases unrun; those three are recorded as a named ``CaseResult.error`` instead. A capture
        failure of type ``CorpusIndexError`` or ``KeyError`` does not propagate (see this module's
        Limitations).
    """
    results = []
    try:
        for case in cases:
            if failure_schedule is not None:
                failure_schedule.failure = case.injected_failure
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
                    logger.warning(
                        "transcript_capture_failed case_id=%s error=%s", case.case_id, exc
                    )
            results.append(result)
    finally:
        if failure_schedule is not None:
            failure_schedule.failure = None
    return tuple(results)
