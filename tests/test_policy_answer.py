"""
Policy Answer Tests
====================

Component: ``app.conversation.policy_answer``. Hermetic: searches the committed corpus files on
disk and reads the loaded policy, no model call and no network.
"""

from __future__ import annotations

# Third-party libraries
import pytest  # Test runner and fixtures

# Local modules
from app.conversation.policy_answer import answer
from app.domain.policy.loader import load_policy
from app.domain.policy.models import DisputeCategory, Policy
from app.retrieval.lexical import LexicalRetriever
from contracts.service_v1.envelope import PolicyValue


@pytest.fixture(scope="module")
def retriever() -> LexicalRetriever:
    """A retriever built from the corpus committed with the repository."""
    return LexicalRetriever.from_corpus()


@pytest.fixture(scope="module")
def policy() -> Policy:
    """The dispute policy committed with the repository."""
    return load_policy()


def test_a_category_specific_question_with_a_known_category_states_the_figure(
    retriever: LexicalRetriever, policy: Policy
) -> None:
    """A filing-window question, with a category already known, answers with that category's own
    figure — never a different category's, and never a figure parsed out of the corpus prose."""
    category = DisputeCategory.UNRECOGNIZED_CHARGE

    result = answer(
        "cual es el plazo para presentar una disputa", "es", category, retriever, policy
    )

    assert result.source is not None
    assert result.source.section_id == "filing-windows"
    days = policy.categories[category].filing_window_days
    assert result.values == (PolicyValue(name="filing_window_days", value=str(days)),)


def test_a_response_time_question_states_the_categorys_own_figure(
    retriever: LexicalRetriever, policy: Policy
) -> None:
    """The other category-specific sections resolve the same way as filing windows."""
    category = DisputeCategory.FRAUD_CLAIM

    result = answer(
        "cuando llega la primera respuesta del banco", "es", category, retriever, policy
    )

    assert result.source is not None
    assert result.source.section_id == "response-time"
    days = policy.first_response_days[category]
    assert result.values == (PolicyValue(name="first_response_days", value=str(days)),)


def test_an_evidence_question_states_the_categorys_own_list(
    retriever: LexicalRetriever, policy: Policy
) -> None:
    """The evidence section resolves to the category's own evidence list, joined as text."""
    category = DisputeCategory.DUPLICATE_CHARGE

    result = answer(
        "que tener listo para la disputa que evidencia necesito", "es", category, retriever, policy
    )

    assert result.source is not None
    assert result.source.section_id == "evidence"
    expected = ", ".join(policy.evidence_required[category])
    assert result.values == (PolicyValue(name="evidence_required", value=expected),)


def test_a_category_specific_question_with_no_known_category_abstains(
    retriever: LexicalRetriever, policy: Policy
) -> None:
    """The same question, with no category yet, carries no source and no figure: a customer who
    has not said which category gets asked, not a guessed number."""
    result = answer("cual es el plazo para presentar una disputa", "es", None, retriever, policy)

    assert result.source is None
    assert result.values == ()


def test_a_general_question_answers_with_its_citation_and_no_figure(
    retriever: LexicalRetriever, policy: Policy
) -> None:
    """A section with no single policy-engine figure still answers, citation only."""
    result = answer("que es esta politica", "es", None, retriever, policy)

    assert result.source is not None
    assert result.source.section_id == "overview"
    assert result.values == ()


def test_an_unmatched_question_abstains(retriever: LexicalRetriever, policy: Policy) -> None:
    """Nothing in the corpus answers a question with no term in common: the result is an
    abstention (no source, no figure), not a store failure."""
    result = answer("xyzzy completely unrelated gibberish nonsense", "es", None, retriever, policy)

    assert result.source is None
    assert result.values == ()


def test_the_answer_carries_a_title_in_every_language(
    retriever: LexicalRetriever, policy: Policy
) -> None:
    """The citation returned always has all three languages' titles, not just the query's own."""
    result = answer("que es esta politica", "es", None, retriever, policy)

    assert result.source is not None
    assert {title.lang for title in result.source.titles} == {"es", "pt", "en"}
