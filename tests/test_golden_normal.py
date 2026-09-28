"""
Golden Set: Normal Category Tests
==================================

Component: ``evals.golden.normal``. Hermetic: the module builds its cases in memory at import
time, from literal data, so no fixture or file access is needed here.
"""

from __future__ import annotations

from collections import Counter

from app.domain.policy.models import ReasonCode
from contracts.service_v1.envelope import Intent
from evals.golden.normal import CASES
from evals.models import CaseCategory


def test_holds_exactly_forty_one_cases() -> None:
    assert len(CASES) == 41


def test_case_ids_are_unique() -> None:
    ids = [case.case_id for case in CASES]
    assert len(ids) == len(set(ids))


def test_seed_refs_are_unique() -> None:
    refs = [case.seed_ref for case in CASES]
    assert len(refs) == len(set(refs))


def test_every_case_is_normal() -> None:
    assert all(case.category is CaseCategory.NORMAL for case in CASES)


def test_no_case_declares_a_safe_behavior() -> None:
    assert all(case.expected_safe_behavior is None for case in CASES)


def test_language_mix_matches_the_evaluation_plan() -> None:
    # plan/docs/evaluation-plan.md's mix table: 20 Spanish, 15 Portuguese, 6 English.
    counts = Counter(case.lang for case in CASES)
    assert counts == {"es": 20, "pt": 15, "en": 6}


def test_every_case_has_provenance_team_generated() -> None:
    assert all(case.provenance == "team_generated" for case in CASES)


def test_filed_dispute_cases_expect_confirm_filing_and_eligible() -> None:
    filed = [case for case in CASES if case.seed_ref.startswith("ops_seed:TRX-")]
    assert len(filed) == 29
    assert all(case.expected_intent is Intent.CONFIRM_FILING for case in filed)
    assert all(case.expected_reason_code is ReasonCode.ELIGIBLE for case in filed)


def test_policy_answer_cases_expect_policy_answer_and_no_reason_code() -> None:
    policy_answers = [case for case in CASES if case.seed_ref.startswith("ops_seed:CLI-")]
    assert len(policy_answers) == 12
    assert all(case.expected_intent is Intent.POLICY_ANSWER for case in policy_answers)
    assert all(case.expected_reason_code is None for case in policy_answers)
