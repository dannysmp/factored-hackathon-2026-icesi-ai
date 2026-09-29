"""
Demo Persona Selection Query Tests
=====================================

Component: ``scripts.select_demo_personas``. The selection and real-engine verification logic is
hermetic (``select_personas`` accepts pre-fetched rows for exactly this reason); only
``_candidates`` itself needs the real ``data/gold/ops_seed`` and
``data/silver/silver/complaints.parquet`` files, which are not committed (``data/`` is
git-ignored) and so are not exercised here.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import pytest

from scripts.select_demo_personas import CandidateRow, main, select_personas

_Shape = tuple[str, str, Decimal, str, bool]

_ELIGIBLE: _Shape = ("Purchase", "Approved", Decimal("100.00"), "Tarjeta Débito", False)
_ABOVE_THRESHOLD: _Shape = ("Purchase", "Approved", Decimal("6000.00"), "Tarjeta Débito", False)
_REPEAT_COMPLAINER: _Shape = ("Purchase", "Approved", Decimal("100.00"), "Tarjeta Débito", True)


def _row(customer_id: str, transaction_id: str, shape: _Shape) -> CandidateRow:
    transaction_type, transaction_status, amount_usd, product_type, is_repeat_complainer = shape
    return (
        customer_id,
        transaction_id,
        date(2026, 5, 1),
        transaction_type,
        transaction_status,
        amount_usd,
        product_type,
        is_repeat_complainer,
    )


def test_selects_one_customer_per_scenario_from_enough_candidates() -> None:
    rows = [
        _row("CLI-1", "TRX-1", _ELIGIBLE),
        _row("CLI-2", "TRX-2", _ELIGIBLE),
        _row("CLI-3", "TRX-3", _ELIGIBLE),
        _row("CLI-4", "TRX-4", _ABOVE_THRESHOLD),
        _row("CLI-5", "TRX-5", _REPEAT_COMPLAINER),
    ]

    mapping = select_personas(rows)

    assert mapping == {
        "ana": "CLI-1",
        "joao": "CLI-2",
        "emma": "CLI-3",
        "carlos": "CLI-4",
        "mariana": "CLI-5",
    }


def test_selection_is_deterministic_across_repeated_calls() -> None:
    """The same candidate rows always pick the same five customers, matching the file's own
    "regenerate the selection" contract — a script whose output changed on every run would be
    useless for that purpose."""
    rows = [
        _row("CLI-3", "TRX-3", _ELIGIBLE),
        _row("CLI-1", "TRX-1", _ELIGIBLE),
        _row("CLI-2", "TRX-2", _ELIGIBLE),
        _row("CLI-4", "TRX-4", _ABOVE_THRESHOLD),
        _row("CLI-5", "TRX-5", _REPEAT_COMPLAINER),
    ]

    first = select_personas(rows)
    second = select_personas(rows)

    assert first == second


def test_never_reuses_a_customer_across_scenarios() -> None:
    """A customer who happens to qualify for two scenarios (both eligible and, separately, an
    above-threshold transaction) is only ever assigned to the first scenario that picks them."""
    rows = [
        _row("CLI-1", "TRX-1", _ELIGIBLE),
        _row("CLI-1", "TRX-1B", _ABOVE_THRESHOLD),  # Same customer, a second transaction.
        _row("CLI-2", "TRX-2", _ELIGIBLE),
        _row("CLI-3", "TRX-3", _ELIGIBLE),
        _row("CLI-4", "TRX-4", _ABOVE_THRESHOLD),
        _row("CLI-5", "TRX-5", _REPEAT_COMPLAINER),
    ]

    mapping = select_personas(rows)

    assert len(set(mapping.values())) == 5
    assert mapping["carlos"] == "CLI-4"


def test_raises_when_too_few_eligible_candidates_exist() -> None:
    rows = [
        _row("CLI-1", "TRX-1", _ELIGIBLE),
        _row("CLI-2", "TRX-2", _ELIGIBLE),
        _row("CLI-4", "TRX-4", _ABOVE_THRESHOLD),
        _row("CLI-5", "TRX-5", _REPEAT_COMPLAINER),
    ]

    with pytest.raises(ValueError, match="need 3 distinct eligible"):
        select_personas(rows)


def test_raises_when_no_above_threshold_candidate_exists() -> None:
    rows = [
        _row("CLI-1", "TRX-1", _ELIGIBLE),
        _row("CLI-2", "TRX-2", _ELIGIBLE),
        _row("CLI-3", "TRX-3", _ELIGIBLE),
        _row("CLI-5", "TRX-5", _REPEAT_COMPLAINER),
    ]

    with pytest.raises(ValueError, match="amount-above-threshold"):
        select_personas(rows)


def test_raises_when_no_repeat_complainer_candidate_exists() -> None:
    rows = [
        _row("CLI-1", "TRX-1", _ELIGIBLE),
        _row("CLI-2", "TRX-2", _ELIGIBLE),
        _row("CLI-3", "TRX-3", _ELIGIBLE),
        _row("CLI-4", "TRX-4", _ABOVE_THRESHOLD),
    ]

    with pytest.raises(ValueError, match="repeat-complainer"):
        select_personas(rows)


def test_main_logs_each_selected_persona_and_returns_0(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        "scripts.select_demo_personas.select_personas",
        lambda: {"ana": "CLI-1", "carlos": "CLI-4"},
    )

    with caplog.at_level(logging.INFO):
        exit_code = main()

    assert exit_code == 0
    assert "persona_selected slug=ana customer_id=CLI-1" in caplog.text
    assert "persona_selected slug=carlos customer_id=CLI-4" in caplog.text


def test_a_candidate_the_real_engine_disagrees_with_is_not_picked() -> None:
    """A row bucketed as an above-threshold escalation by amount alone, but whose transaction
    date is well outside the filing window, would pass a naive SQL-bucket-only filter; the real
    ``evaluate_dispute`` call inside ``select_personas`` returns ``INELIGIBLE`` for it (a filing
    window violation), not ``ESCALATE``, so it is correctly rejected rather than picked — proving
    the verification is a real engine call, not just trusting which bucket a row landed in."""
    expired_threshold_row: CandidateRow = (
        "CLI-4",
        "TRX-4",
        date(2020, 1, 1),  # Far outside any filing window as of the reference date.
        "Purchase",
        "Approved",
        Decimal("6000.00"),
        "Tarjeta Débito",
        False,
    )
    rows = [
        _row("CLI-1", "TRX-1", _ELIGIBLE),
        _row("CLI-2", "TRX-2", _ELIGIBLE),
        _row("CLI-3", "TRX-3", _ELIGIBLE),
        expired_threshold_row,
        _row("CLI-5", "TRX-5", _REPEAT_COMPLAINER),
    ]

    with pytest.raises(ValueError, match="amount-above-threshold"):
        select_personas(rows)
