"""
Golden Set: Adversarial Category Tests
=========================================

Component: ``evals.golden.adversarial``. Hermetic: the module builds its cases in memory at
import time, from literal data, so no fixture or file access is needed here.
"""

from __future__ import annotations

from collections import Counter

from app.domain.policy.models import ReasonCode
from contracts.service_v1.envelope import Intent
from evals.golden.adversarial import CASES
from evals.models import CaseCategory, SafeBehavior


def test_holds_exactly_thirty_two_cases() -> None:
    assert len(CASES) == 32


def test_case_ids_are_unique() -> None:
    ids = [case.case_id for case in CASES]
    assert len(ids) == len(set(ids))


def test_every_case_is_adversarial() -> None:
    assert all(case.category is CaseCategory.ADVERSARIAL for case in CASES)


def test_every_case_declares_a_safe_behavior() -> None:
    # Case.__post_init__ already enforces this structurally; this test protects the invariant
    # from a future refactor that might bypass the constructor.
    assert all(case.expected_safe_behavior is not None for case in CASES)


def test_safe_behavior_mix_matches_the_six_subtypes() -> None:
    counts = Counter(case.expected_safe_behavior for case in CASES)
    assert counts[SafeBehavior.REFUSE] == 16  # message injection (6) + poisoned field (4)
    assert counts[SafeBehavior.RE_AUTH] == 4  # expired session
    assert counts[SafeBehavior.HANDOFF] == 10  # tool failure (6) + orphan/unknown-amount (4)
    assert counts[SafeBehavior.CLARIFY] == 2  # null merchant name


def test_eval_bank_scenarios_are_shared_not_unique() -> None:
    # Only 4 distinct eval_bank rows exist; 10 cases reference them, so seed_ref uniqueness
    # does not hold globally the way it does for every other category module.
    eval_bank_refs = [case.seed_ref for case in CASES if case.seed_ref.startswith("eval_bank:")]
    assert len(eval_bank_refs) == 10
    assert len(set(eval_bank_refs)) == 4


def test_ops_seed_anchored_refs_are_unique() -> None:
    ops_seed_refs = [case.seed_ref for case in CASES if case.seed_ref.startswith("ops_seed:")]
    assert len(ops_seed_refs) == 22
    assert len(ops_seed_refs) == len(set(ops_seed_refs))


def test_provenance_matches_the_seed_ref_kind() -> None:
    for case in CASES:
        if case.seed_ref.startswith("eval_bank:"):
            assert case.provenance == "injected"
        else:
            assert case.provenance == "team_generated"


def test_unknown_amount_cases_carry_the_escalate_reason_code() -> None:
    unknown_amount = [
        case for case in CASES if case.seed_ref == "eval_bank:TRX-EVALBANK-UNKNOWN-AMOUNT"
    ]
    assert len(unknown_amount) == 2
    assert all(
        case.expected_reason_code is ReasonCode.ESCALATE_AMOUNT_UNKNOWN for case in unknown_amount
    )
    assert all(case.expected_intent is Intent.HANDOFF for case in unknown_amount)


def test_other_cases_declare_no_reason_code() -> None:
    non_unknown_amount = [
        case for case in CASES if case.seed_ref != "eval_bank:TRX-EVALBANK-UNKNOWN-AMOUNT"
    ]
    assert all(case.expected_reason_code is None for case in non_unknown_amount)
