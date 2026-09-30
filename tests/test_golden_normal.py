"""
Golden Set: Normal Category Tests
==================================

Component: ``evals.golden.normal``. Hermetic: the module builds its cases in memory at import
time, from literal data, so no fixture or file access is needed here.
"""

from __future__ import annotations

from collections import Counter

from app.domain.policy.models import DisputeCategory, ReasonCode
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


#: Each filed-dispute case_id's own infix names its dispute category; a case moved into the wrong
#: group in evals/golden/normal.py would state a category its own id disagrees with.
_CATEGORY_INFIX = {
    "unrecognized": DisputeCategory.UNRECOGNIZED_CHARGE,
    "wrongamt": DisputeCategory.WRONG_AMOUNT,
    "duplicate": DisputeCategory.DUPLICATE_CHARGE,
    "service": DisputeCategory.SERVICE_NOT_RECEIVED,
}


def test_filed_dispute_cases_declare_the_category_their_own_case_id_names() -> None:
    filed = [case for case in CASES if case.seed_ref.startswith("ops_seed:TRX-")]
    counts = Counter(case.expected_category for case in filed)
    assert counts == {
        DisputeCategory.UNRECOGNIZED_CHARGE: 10,
        DisputeCategory.WRONG_AMOUNT: 7,
        DisputeCategory.DUPLICATE_CHARGE: 5,
        DisputeCategory.SERVICE_NOT_RECEIVED: 7,
    }
    for case in filed:
        infix = next(infix for infix in _CATEGORY_INFIX if infix in case.case_id)
        assert case.expected_category is _CATEGORY_INFIX[infix], (
            f"{case.case_id} declares {case.expected_category}, but its own id names {infix}"
        )


def test_policy_answer_cases_expect_policy_answer_and_no_reason_code() -> None:
    policy_answers = [case for case in CASES if case.seed_ref.startswith("ops_seed:CLI-")]
    assert len(policy_answers) == 12
    assert all(case.expected_intent is Intent.POLICY_ANSWER for case in policy_answers)
    assert all(case.expected_reason_code is None for case in policy_answers)
