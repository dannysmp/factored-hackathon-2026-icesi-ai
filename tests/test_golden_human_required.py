"""
Golden Set: Human-required Category Tests
==========================================

Component: ``evals.golden.human_required``. Hermetic: the module builds its cases in memory at
import time, from literal data, so no fixture or file access is needed here.
"""

from __future__ import annotations

# Standard libraries
from collections import Counter

# Local modules
from app.domain.policy.models import ReasonCode
from contracts.service_v1.envelope import Intent
from evals.golden.human_required import CASES
from evals.models import CaseCategory


def test_holds_exactly_twenty_two_cases() -> None:
    assert len(CASES) == 22


def test_case_ids_are_unique() -> None:
    ids = [case.case_id for case in CASES]
    assert len(ids) == len(set(ids))


def test_every_case_is_human_required() -> None:
    assert all(case.category is CaseCategory.HUMAN_REQUIRED for case in CASES)


def test_no_case_declares_a_safe_behavior() -> None:
    # Human-required is not the adversarial category; Case's own invariant already forbids
    # a safe behavior here, but a passing suite should show that no case tries to.
    assert all(case.expected_safe_behavior is None for case in CASES)


def test_language_mix_matches_the_evaluation_plan() -> None:
    # plan/docs/evaluation-plan.md's mix table: 10 Spanish, 8 Portuguese, 4 English.
    counts = Counter(case.lang for case in CASES)
    assert counts == {"es": 10, "pt": 8, "en": 4}


def test_every_case_routes_to_handoff() -> None:
    # Every human-required case's correct reply hands off to a person, by definition of the row.
    assert all(case.expected_intent is Intent.HANDOFF for case in CASES)


def test_reason_code_mix_matches_the_four_subtypes() -> None:
    counts = Counter(case.expected_reason_code for case in CASES)
    assert counts[ReasonCode.ESCALATE_FRAUD_CLAIM] == 6
    assert counts[ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD] == 6
    assert counts[ReasonCode.ESCALATE_REPEAT_COMPLAINER] == 4
    assert counts[None] == 6  # the direct ask-for-a-human cases select no dispute category


def test_seed_refs_are_unique_ops_seed_transactions() -> None:
    refs = [case.seed_ref for case in CASES]
    assert len(refs) == len(set(refs))
    assert all(ref.startswith("ops_seed:TRX-") for ref in refs)


def test_every_case_has_provenance_team_generated() -> None:
    # The source call-transcript data carries no dispute language at all (see the module's
    # Limitations); no case here can honestly claim `observed` wording.
    assert all(case.provenance == "team_generated" for case in CASES)


def test_every_case_has_two_scripted_turns() -> None:
    assert all(len(case.user_turns) == 2 for case in CASES)
