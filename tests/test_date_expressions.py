"""
Date Expression Resolution Tests
===================================

Component: ``app.conversation.date_expressions``. Hermetic and pure: no clock, no model — every
case supplies its own fixed reference date.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.conversation.date_expressions import resolve
from contracts.service_v1.envelope import DateSource, Lang

_REFERENCE_DATE = date(2026, 6, 18)  # A Thursday.


@pytest.mark.parametrize(
    ("expression", "language", "expected_date"),
    [
        ("hoy", "es", date(2026, 6, 18)),
        ("ayer", "es", date(2026, 6, 17)),
        ("anteayer", "es", date(2026, 6, 16)),
        ("antes de ayer", "es", date(2026, 6, 16)),
        ("hoje", "pt", date(2026, 6, 18)),
        ("ontem", "pt", date(2026, 6, 17)),
        ("anteontem", "pt", date(2026, 6, 16)),
        ("today", "en", date(2026, 6, 18)),
        ("yesterday", "en", date(2026, 6, 17)),
        ("the day before yesterday", "en", date(2026, 6, 16)),
    ],
)
def test_a_relative_day_term_resolves_against_the_reference_date(
    expression: str, language: Lang, expected_date: date
) -> None:
    result = resolve(expression, language=language, reference_date=_REFERENCE_DATE)

    assert result == (expected_date, DateSource.RELATIVE)


@pytest.mark.parametrize(
    ("expression", "language", "expected_date"),
    [
        # 2026-06-18 is a Thursday: "lunes"/"monday" the same week is 2026-06-15.
        ("lunes", "es", date(2026, 6, 15)),
        ("segunda", "pt", date(2026, 6, 15)),
        ("segunda-feira", "pt", date(2026, 6, 15)),
        ("monday", "en", date(2026, 6, 15)),
        # The reference date's own weekday resolves to itself, not a week back.
        ("jueves", "es", date(2026, 6, 18)),
        ("thursday", "en", date(2026, 6, 18)),
        # A weekday later in the week than the reference date's own resolves to last week's.
        ("viernes", "es", date(2026, 6, 12)),
        ("friday", "en", date(2026, 6, 12)),
    ],
)
def test_a_weekday_name_resolves_to_its_most_recent_occurrence(
    expression: str, language: Lang, expected_date: date
) -> None:
    result = resolve(expression, language=language, reference_date=_REFERENCE_DATE)

    assert result == (expected_date, DateSource.RELATIVE)


@pytest.mark.parametrize(
    ("expression", "language", "expected_date"),
    [
        ("el 3", "es", date(2026, 6, 3)),
        ("el dia 3", "es", date(2026, 6, 3)),
        ("dia 3", "pt", date(2026, 6, 3)),
        ("the 3rd", "en", date(2026, 6, 3)),
        ("3rd", "en", date(2026, 6, 3)),
        # A day-of-month later than the reference date's own day falls back to the prior month.
        ("el 25", "es", date(2026, 5, 25)),
        ("the 25th", "en", date(2026, 5, 25)),
    ],
)
def test_a_day_of_month_phrase_resolves_to_its_most_recent_occurrence(
    expression: str, language: Lang, expected_date: date
) -> None:
    result = resolve(expression, language=language, reference_date=_REFERENCE_DATE)

    assert result == (expected_date, DateSource.PARTIAL)


@pytest.mark.parametrize(
    ("expression", "expected_date"),
    [
        # Day first, in every language, per AC-E5-16 — including English, overriding its usual
        # month-first convention.
        ("03/04", date(2026, 4, 3)),
        ("3/4", date(2026, 4, 3)),
        ("15/01", date(2026, 1, 15)),
        ("03/04/2025", date(2025, 4, 3)),
        ("03/04/25", date(2025, 4, 3)),
    ],
)
def test_a_numeric_date_is_read_day_first_regardless_of_language(
    expression: str, expected_date: date
) -> None:
    for language in ("es", "pt", "en"):
        result = resolve(expression, language=language, reference_date=_REFERENCE_DATE)

        assert result == (expected_date, DateSource.NUMERIC)


def test_a_numeric_date_with_no_year_falls_back_to_last_year_if_that_would_be_in_the_future() -> (
    None
):
    """31/12 with no year, against a reference date of 2026-06-18, cannot mean 2026-12-31 (a
    transaction is never in the future) — it means 2025-12-31."""
    result = resolve("31/12", language="es", reference_date=_REFERENCE_DATE)

    assert result == (date(2025, 12, 31), DateSource.NUMERIC)


def test_a_numeric_date_needs_no_language_to_resolve() -> None:
    """The day-first regex needs no closed vocabulary, so it resolves even with no language."""
    result = resolve("03/04", language=None, reference_date=_REFERENCE_DATE)

    assert result == (date(2026, 4, 3), DateSource.NUMERIC)


def test_an_unrecognized_expression_resolves_to_nothing() -> None:
    """The same "nothing stated" outcome as no date expression at all — never a guess."""
    assert resolve("algo raro", language="es", reference_date=_REFERENCE_DATE) is None


def test_a_vague_range_is_deliberately_left_unresolved() -> None:
    """ "Last week" names no single day; picking one would be inventing a fact the customer did
    not give, so it stays unresolved, the same as an expression this table never learned."""
    assert resolve("semana pasada", language="es", reference_date=_REFERENCE_DATE) is None
    assert resolve("last week", language="en", reference_date=_REFERENCE_DATE) is None


def test_a_relative_term_needs_a_language_to_resolve() -> None:
    """Unlike the numeric case, the closed vocabulary is per language: with no language at all,
    a relative or partial expression cannot be matched against any table."""
    assert resolve("ayer", language=None, reference_date=_REFERENCE_DATE) is None


def test_a_day_of_month_no_month_ever_has_resolves_to_nothing() -> None:
    """A day number invalid in every month exhausts the bounded month-by-month search without
    ever finding one — an honest gap ``None`` states plainly, rather than picking a wrong day."""
    result = resolve("el 32", language="es", reference_date=_REFERENCE_DATE)

    assert result is None


def test_a_day_of_month_search_gives_up_after_its_bounded_number_of_months() -> None:
    """A real day (30th) still resolves once the search reaches a month that has one — proving
    the bounded walk-back itself works, not just the "day never exists" case above."""
    result = resolve("el 30", language="es", reference_date=date(2026, 3, 1))

    assert result == (date(2026, 1, 30), DateSource.PARTIAL)


def test_a_day_of_month_search_crosses_a_year_boundary() -> None:
    """January's own walk-back reaches December of the year before — the search wraps the year,
    not just the month."""
    result = resolve("el 29", language="es", reference_date=date(2026, 1, 15))

    assert result == (date(2025, 12, 29), DateSource.PARTIAL)


def test_a_numeric_date_with_an_explicit_year_that_does_not_exist_resolves_to_nothing() -> None:
    """30/02 names no real day even with a year stated: February never has a 30th."""
    assert resolve("30/02/2026", language="es", reference_date=_REFERENCE_DATE) is None


def test_a_numeric_date_with_an_explicit_future_year_resolves_to_nothing() -> None:
    """A transaction date is never in the future — an explicit year does not override that, and
    unlike the no-year case there is no sensible earlier year to fall back to: the customer stated
    this year themselves, so guessing a different one would be inventing a fact, not resolving
    one."""
    assert resolve("03/04/2027", language="es", reference_date=_REFERENCE_DATE) is None


def test_a_numeric_date_with_no_year_that_never_exists_resolves_to_nothing() -> None:
    """The same day-never-exists case, without a stated year: neither this year's nor a fallback
    year's construction can succeed, so there is no year to fall back to."""
    assert resolve("30/02", language="es", reference_date=_REFERENCE_DATE) is None


def test_a_numeric_date_falling_back_a_year_can_itself_not_exist() -> None:
    """29/02 in a leap reference year, later in the year than the reference date, tries the year
    before it — which can itself lack a 29th of February, resolving to nothing rather than a
    wrong day."""
    assert resolve("29/02", language="es", reference_date=date(2024, 1, 1)) is None
