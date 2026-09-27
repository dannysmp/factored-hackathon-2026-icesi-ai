"""
Golden Set: Multilingual Category Tests
==========================================

Component: ``evals.golden.multilingual``. Hermetic: the module builds its cases in memory at
import time, from literal data, so no fixture or file access is needed here.
"""

from __future__ import annotations

from contracts.service_v1.envelope import Intent
from evals.golden.multilingual import CASES
from evals.models import CaseCategory


def test_holds_exactly_ten_cases() -> None:
    assert len(CASES) == 10


def test_case_ids_are_unique() -> None:
    ids = [case.case_id for case in CASES]
    assert len(ids) == len(set(ids))


def test_seed_refs_are_unique_customers() -> None:
    refs = [case.seed_ref for case in CASES]
    assert len(refs) == len(set(refs))
    assert all(ref.startswith("ops_seed:CLI-") for ref in refs)


def test_every_case_is_multilingual() -> None:
    assert all(case.category is CaseCategory.MULTILINGUAL for case in CASES)


def test_no_case_declares_a_safe_behavior() -> None:
    assert all(case.expected_safe_behavior is None for case in CASES)


def test_lang_is_one_of_the_three_supported_languages() -> None:
    # The mix table gives no per-language split for this category (just a total of 10); the
    # invariant here is only that every case declares a supported language, per the architect's
    # ruling that `lang` names the conversation's sticky reply language.
    assert all(case.lang in {"es", "pt", "en"} for case in CASES)


def test_every_case_has_provenance_team_generated() -> None:
    assert all(case.provenance == "team_generated" for case in CASES)


def test_every_case_expects_policy_answer_with_no_reason_code() -> None:
    assert all(case.expected_intent is Intent.POLICY_ANSWER for case in CASES)
    assert all(case.expected_reason_code is None for case in CASES)


def test_every_case_has_exactly_one_scripted_turn() -> None:
    assert all(len(case.user_turns) == 1 for case in CASES)
