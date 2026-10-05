"""
Judge Grounding Facts
======================

Overview
--------
Assembles the ``facts_and_sources`` text the LLM judge (``evals.judge``) and the human
judge-validation sample both score a case's replies against: what a grounded reply is allowed to
state. Never reads the running conversation's own envelope (the grounding boundary, the same one
``evals.scoring``'s deterministic checks already refuse to reopen "from outside the process") —
every fact here comes from either the store's own tables, by the transcript's own ``session_id``
(the identical "two vantage points" precedent ``evals.scoring``'s ``_case_row_exists`` and
``_handoff_ticket_is_backed`` already use), or the golden-set case's own authored, committed
``expected_policy_section_id``. Also attaches this text, and the run's own reply text, to a finished
``CaseResult`` (``attach_masked_transcript``) so a judge or a human rater can read both long after
the run that produced them has ended.

Scope
-----
In: the transaction and filed-case facts a session's own stored rows carry; the policy corpus
section a policy-answer case declares it is grounded in; attaching both, and the run's own reply
text, to a ``CaseResult`` for later reading.
Out: a fact only the running conversation's own envelope would know (a risk score, an NLU
confidence, a retrieval trace's ranking) — none of those are "facts the reply must cite," they are
the reasoning that produced the reply, which grounding does not score (see Limitations).

Design Principles
-----------------
- **Two vantage points, never a third.** A direct, read-only ``psycopg`` query against the store's
  own tables, exactly as ``evals.scoring`` already queries them for its own checks — never through
  ``app.tools.PostgresToolPort``, which would write a spurious audit record into the log the
  conversation under test itself uses.
- **A case with nothing to ground against is not an error.** A case whose golden-set record names
  no policy section and whose session filed no case (an adversarial refusal, a still-open
  clarification, an abstention) has no known facts to check a reply against; grounding then
  means "invents nothing," not "cites something," and the assembled text says so explicitly rather
  than silently returning an empty string a report reader could mistake for an assembly failure.
- **Attachment masks unconditionally, with no carve-out for trusted-origin text.** Both attached
  fields pass through ``redact_pan`` before they are stored, exactly like every other field this
  project ever sends an LLM or writes to a log — the fact that ``facts_and_sources`` originates
  from the store rather than the customer is not a reason to skip the same egress control everyone
  else's outbound text already goes through, and ``redact_pan`` is a safe no-op when nothing
  card-shaped is present.

Runtime Contract
-----------------
``assemble_facts_and_sources(dsn, transcript) -> str``.
``attach_masked_transcript(dsn, transcript, result) -> CaseResult``: the caller's own opt-in step
(``evals.runner.runner.run_cases``'s ``capture_transcripts`` flag) that fills a ``CaseResult``'s
``reply_text``/``facts_and_sources`` fields for a judge or a human rater to read later, once the
run itself is long over. Never called by ``evals.scoring.score_case``, which stays exactly as
narrow as this module's own Scope already draws it.

Limitations
-----------
Only the transaction behind a case the session actually filed is included; a status-inquiry case
whose transaction already existed in seeded state before this run, but whose session never filed a
new case, is not resolved here (the transcript alone does not name that transaction id without
reopening the envelope). Retrieval-quality judgment — whether the system found the *best* of several
plausible sections — is out of scope for grounding; the deterministic "recall at three of policy
retrieval" metric covers that separately.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass, replace
from decimal import Decimal

# Third-party libraries
import psycopg

# Local modules
from app.llm.masking import redact_pan  # The one PAN-shaped-digit-run detector this project trusts
from app.retrieval.corpus_index import load_chunks
from evals.metrics import CaseResult
from evals.models import Case
from evals.scoring import RunTranscript

#: The grounding text of a conversation with no filed transaction and no declared policy section.
NO_KNOWN_FACTS = (
    "No case-specific facts are on record for this conversation: a grounded reply here invents "
    "nothing rather than citing something."
)


@dataclass(frozen=True, slots=True)
class _FiledTransactionFacts:
    """The trusted transaction fields behind a case this session actually filed."""

    merchant_name: str | None
    amount: Decimal
    currency: str
    transaction_type: str | None
    transaction_status: str


def _query_filed_transaction(dsn: str, session_id: str) -> _FiledTransactionFacts | None:
    """The transaction behind the case this session filed, if any.

    See this module's Limitations for what a status-inquiry-only session does not resolve.
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT t.merchant_name, t.amount, t.currency, t.transaction_type, "
            "t.transaction_status "
            "FROM cases c JOIN transactions t ON c.transaction_id = t.transaction_id "
            "WHERE c.session_id = %s LIMIT 1",
            (session_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    merchant_name, amount, currency, transaction_type, transaction_status = row
    return _FiledTransactionFacts(
        merchant_name=merchant_name,
        amount=amount,
        currency=currency,
        transaction_type=transaction_type,
        transaction_status=transaction_status,
    )


def _render_transaction_facts(facts: _FiledTransactionFacts) -> str:
    merchant = facts.merchant_name or "no merchant on record"
    return (
        "Filed case's transaction (trusted, from the store):\n"
        f"- amount: {facts.amount} {facts.currency}\n"
        f"- merchant: {merchant}\n"
        f"- type: {facts.transaction_type or 'unknown'}\n"
        f"- status: {facts.transaction_status}"
    )


def _render_policy_section(case: Case) -> str:
    """The exact corpus chunk a policy-answer case declares it is grounded in.

    Raises
    ------
    CorpusIndexError
        The corpus file for ``case.lang`` cannot be read (a real assembly failure, never
        swallowed into ``NO_KNOWN_FACTS`` — the golden set's own tests already prove every
        declared section id resolves, so a failure here means the corpus itself changed).
    KeyError
        ``case.expected_policy_section_id`` does not resolve in ``case.lang``'s corpus (the same
        real-failure reasoning as ``CorpusIndexError``).
    """
    section_id = case.expected_policy_section_id
    for chunk in load_chunks(case.lang):
        if chunk.section_id == section_id:
            return f"Policy section '{chunk.section_id}' ({chunk.title}):\n{chunk.body}"
    raise KeyError(f"section {section_id!r} does not resolve in the {case.lang} corpus")


def assemble_facts_and_sources(dsn: str, transcript: RunTranscript) -> str:
    """The grounding text a judge or human rater scores ``transcript``'s replies against.

    Raises
    ------
    CorpusIndexError, KeyError
        The case declares a policy section that does not resolve (see ``_render_policy_section``).
    """
    parts: list[str] = []
    if transcript.case.expected_policy_section_id is not None:
        parts.append(_render_policy_section(transcript.case))
    transaction = _query_filed_transaction(dsn, transcript.session_id)
    if transaction is not None:
        parts.append(_render_transaction_facts(transaction))
    if not parts:
        return NO_KNOWN_FACTS
    return "\n\n".join(parts)


def attach_masked_transcript(dsn: str, transcript: RunTranscript, result: CaseResult) -> CaseResult:
    """Fill ``result``'s ``reply_text``/``facts_and_sources`` fields for a later judge or rater.

    Every reply ``transcript`` recorded, joined in order, and this same transcript's own grounding
    text (``assemble_facts_and_sources``) — both PAN-masked before they are attached, the same
    unconditional egress control every other outbound LLM field already goes through (the
    PII-minimization rule draws no exception for text this module already trusts came from the store
    rather than the customer).

    Raises
    ------
    CorpusIndexError, KeyError
        The case declares a policy section that does not resolve (see
        ``_render_policy_section``); the caller decides whether that aborts capture for this one
        case or the whole batch.
    """
    reply_text = redact_pan("\n\n".join(reply.reply for reply in transcript.replies)).masked
    facts_and_sources = redact_pan(assemble_facts_and_sources(dsn, transcript)).masked
    return replace(result, reply_text=reply_text, facts_and_sources=facts_and_sources)
