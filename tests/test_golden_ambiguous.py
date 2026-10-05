"""
Golden Set: Ambiguous Category Tests
======================================

Component: ``evals.golden.ambiguous``. Hermetic: the module builds its cases in memory at
import time, from literal data, so no fixture or file access is needed here.
"""

from __future__ import annotations

from collections import Counter

from contracts.service_v1.envelope import Intent
from evals.golden.ambiguous import CASES
from evals.models import CaseCategory


def test_holds_exactly_seventeen_cases() -> None:
    assert len(CASES) == 17


def test_case_ids_are_unique() -> None:
    ids = [case.case_id for case in CASES]
    assert len(ids) == len(set(ids))


def test_seed_refs_are_unique_customers() -> None:
    refs = [case.seed_ref for case in CASES]
    assert len(refs) == len(set(refs))
    assert all(ref.startswith("ops_seed:CLI-") for ref in refs)


def test_every_case_is_ambiguous() -> None:
    assert all(case.category is CaseCategory.AMBIGUOUS for case in CASES)


def test_no_case_declares_a_safe_behavior() -> None:
    assert all(case.expected_safe_behavior is None for case in CASES)


def test_language_mix_is_the_designed_split() -> None:
    # The designed language mix: 8 Spanish, 6 Portuguese, 3 English.
    counts = Counter(case.lang for case in CASES)
    assert counts == {"es": 8, "pt": 6, "en": 3}


def test_every_case_has_provenance_team_generated() -> None:
    assert all(case.provenance == "team_generated" for case in CASES)


def test_every_case_expects_clarify_with_no_reason_code() -> None:
    assert all(case.expected_intent is Intent.CLARIFY for case in CASES)
    assert all(case.expected_reason_code is None for case in CASES)


def test_every_case_has_exactly_one_scripted_turn() -> None:
    assert all(len(case.user_turns) == 1 for case in CASES)
