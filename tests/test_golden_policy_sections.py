"""
Golden Set Policy-Section Grounding Tests
============================================

Component: ``evals.golden`` cases declaring ``expected_policy_section_id``, checked against the
real, committed policy corpus. ``evals.models.Case`` stays I/O-free by its own design, so this is
where a declared section id is actually resolved, the same "not validated at construction, checked
by a test instead" split ``Case``'s own docstring already states for ``seed_ref``.
"""

from __future__ import annotations

# Third-party libraries
import pytest

# Local modules
from app.retrieval.corpus_index import load_chunks
from contracts.service_v1.envelope import LANGUAGES, Intent, Lang
from evals.golden.case_sheet import ALL_CASES


def test_every_policy_answer_case_declares_a_section() -> None:
    for case in ALL_CASES:
        if case.expected_intent is Intent.POLICY_ANSWER:
            assert case.expected_policy_section_id is not None, case.case_id


def test_every_declared_section_resolves_in_its_own_language() -> None:
    section_ids = {lang: {chunk.section_id for chunk in load_chunks(lang)} for lang in LANGUAGES}
    for case in ALL_CASES:
        if case.expected_policy_section_id is None:
            continue
        assert case.expected_policy_section_id in section_ids[case.lang], case.case_id


@pytest.mark.parametrize("lang", list(LANGUAGES))
def test_every_corpus_section_id_is_the_same_across_languages(lang: Lang) -> None:
    # The corpus generator's own invariant (app.retrieval.corpus_index's docstring): the section
    # identifier and order are the same in every language, so a section declared against one
    # language's corpus is meaningful read against any of the others too.
    es_ids = {chunk.section_id for chunk in load_chunks("es")}
    assert {chunk.section_id for chunk in load_chunks(lang)} == es_ids
