"""
Policy Answer
=============

Overview
--------
Turns a policy question into the facts and source the renderer needs (ADR-16): a search for the
best-matching corpus section, and — for the handful of sections whose answer is a number the
policy engine itself owns — the structured value from the loaded ``Policy``, never from the
corpus text. Retrieval finds *which* section answers the question; it never supplies the figure
that goes in the reply, so a number can never drift between what the engine enforces and what a
citation says (the architecture document's own retrieval boundary).

Scope
-----
In: ``answer(query, lang, category, retriever, policy) -> PolicyAnswer``.
Out: running the search itself (``app.retrieval.lexical.Retriever``), loading the policy
(``app.domain.policy.loader``), building the envelope from the result (the dialogue controller).

Design Principles
-----------------
- **Retrieval never feeds a figure.** Only ``section_id`` from the best hit selects which
  ``Policy`` field to read, if any; the figure itself always comes from ``policy``, keyed by the
  conversation's own known category — never parsed out of the corpus body.
- **A category-specific section with no known category abstains**, rather than picking one
  category's figure to guess with or answering with all five: a customer who has not yet named a
  category gets asked to, not a number that might be wrong for their situation. Filing this as an
  answer with no source and no figure is drawn straight from the abstention behavior
  ``contracts.service_v1.envelope`` already defines for the ``abstain`` intent.
- **A general section (no category-specific figure) answers with its citation alone** — the
  section title itself is the informative part of the reply for a question like "what is this
  policy" or "when does a person review this."

Runtime Contract
----------------
``PolicyAnswer`` (``source: SourceRef | None``, ``values: tuple[PolicyValue, ...]``).
``answer(query, lang, category, retriever, policy) -> PolicyAnswer``.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Callable  # Signature for the per-section figure builders
from dataclasses import dataclass  # Immutable result of one lookup

# Local modules
from app.domain.policy.models import DisputeCategory, Policy
from app.retrieval.lexical import LexicalRetriever
from contracts.service_v1.envelope import Lang, PolicyValue, SourceRef


@dataclass(frozen=True, slots=True)
class PolicyAnswer:
    """What a policy question resolves to: a citation, its figures, or neither (abstain)."""

    source: SourceRef | None
    values: tuple[PolicyValue, ...] = ()


def _filing_window(category: DisputeCategory, policy: Policy) -> PolicyValue:
    days = policy.categories[category].filing_window_days
    return PolicyValue(name="filing_window_days", value=str(days))


def _response_time(category: DisputeCategory, policy: Policy) -> PolicyValue:
    return PolicyValue(name="first_response_days", value=str(policy.first_response_days[category]))


def _evidence(category: DisputeCategory, policy: Policy) -> PolicyValue:
    evidence = ", ".join(policy.evidence_required[category])
    return PolicyValue(name="evidence_required", value=evidence)


# The corpus sections whose answer is a single policy-engine figure, and the builder that reads
# it. A section named here needs a known category to answer with a figure; any other section (an
# overview, a description of the confirmation or human-review step) answers with its citation
# alone, no figure attached. Deriving ``_CATEGORY_SECTIONS`` from these keys keeps the two in sync
# by construction — there is no separate list that could drift.
_FIGURE_BUILDERS: dict[str, Callable[[DisputeCategory, Policy], PolicyValue]] = {
    "filing-windows": _filing_window,
    "response-time": _response_time,
    "evidence": _evidence,
}
_CATEGORY_SECTIONS = frozenset(_FIGURE_BUILDERS)


def answer(
    query: str,
    lang: Lang,
    category: DisputeCategory | None,
    retriever: LexicalRetriever,
    policy: Policy,
) -> PolicyAnswer:
    """The best answer ``retriever`` and ``policy`` together give ``query``, or an abstention."""
    hits = retriever.search(query, lang)
    if not hits:
        return PolicyAnswer(source=None)

    section_id = hits[0].chunk.section_id
    if section_id not in _CATEGORY_SECTIONS:
        return PolicyAnswer(source=retriever.source_ref_for(section_id))

    if category is None:
        return PolicyAnswer(source=None)

    figure = _FIGURE_BUILDERS[section_id](category, policy)
    return PolicyAnswer(source=retriever.source_ref_for(section_id), values=(figure,))
