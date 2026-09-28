"""
Smoke Case Selection Tests
============================

Component: ``evals.runner.smoke``. Fully hermetic — no store, no network.
"""

from __future__ import annotations

import pytest

from evals.golden.adversarial import CASES as ADVERSARIAL_CASES
from evals.runner.smoke import SMOKE_CASE_IDS, smoke_cases


def test_smoke_case_ids_names_exactly_all_injection_and_authz_subtypes() -> None:
    prefixes = {case_id.rsplit("-", 2)[0] for case_id in SMOKE_CASE_IDS}
    assert prefixes == {"adv-injection", "adv-poisoned", "adv-unauthorized"}
    assert len(SMOKE_CASE_IDS) == 16


def test_smoke_cases_returns_one_case_per_id_with_no_duplicates_or_omissions() -> None:
    cases = smoke_cases()

    assert {case.case_id for case in cases} == SMOKE_CASE_IDS
    assert len(cases) == len(SMOKE_CASE_IDS)


def test_smoke_cases_preserves_the_golden_sets_own_declared_order() -> None:
    """A frozenset has no iteration order of its own; the returned order must come from the
    golden set's own tuple, not from iterating SMOKE_CASE_IDS."""
    expected = tuple(case for case in ADVERSARIAL_CASES if case.case_id in SMOKE_CASE_IDS)

    assert smoke_cases() == expected


def test_smoke_cases_raises_loudly_when_a_named_id_is_missing_from_the_golden_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the missing-id check actually fires, rather than the tuple comprehension silently
    returning fewer cases than SMOKE_CASE_IDS names."""
    thinned = tuple(case for case in ADVERSARIAL_CASES if case.case_id != "adv-injection-es-01")
    monkeypatch.setattr("evals.runner.smoke.ADVERSARIAL_CASES", thinned)

    with pytest.raises(ValueError, match="adv-injection-es-01"):
        smoke_cases()
