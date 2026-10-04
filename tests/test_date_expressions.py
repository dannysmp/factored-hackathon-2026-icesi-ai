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


def test_a_numeric_date_with_an_explicit_year_matching_the_reference_date_resolves() -> None:
    """The reference date itself is not "in the future" relative to itself: an explicit year
    landing exactly on it must still resolve, not be rejected by an off-by-one boundary check."""
    result = resolve("18/06/2026", language="es", reference_date=_REFERENCE_DATE)

    assert result == (_REFERENCE_DATE, DateSource.NUMERIC)


def test_a_numeric_date_with_no_year_matching_the_reference_date_resolves() -> None:
    """The same same-day boundary on the no-year branch: the reference date's own day must resolve
    to itself, not be mistaken for "in the future" and pushed back a year it doesn't need."""
    result = resolve("18/06", language="es", reference_date=_REFERENCE_DATE)

    assert result == (_REFERENCE_DATE, DateSource.NUMERIC)


@pytest.mark.parametrize(
    ("expression", "language", "expected_date"),
    [
        ("June 3rd", "en", date(2026, 6, 3)),
        ("on June 3", "en", date(2026, 6, 3)),
        ("3 June", "en", date(2026, 6, 3)),
        ("the 3rd of June", "en", date(2026, 6, 3)),
        ("Jun. 3", "en", date(2026, 6, 3)),
        ("sept 9", "en", date(2025, 9, 9)),
        ("3 de junio", "es", date(2026, 6, 3)),
        ("el 3 de junio", "es", date(2026, 6, 3)),
        ("21 de abril", "es", date(2026, 4, 21)),
        ("dia 21 de abril", "pt", date(2026, 4, 21)),
        ("21 de março", "pt", date(2026, 3, 21)),
        ("3 de junho", "pt", date(2026, 6, 3)),
        ("no dia 21 de abril", "pt", date(2026, 4, 21)),
        ("em 21 de abril", "pt", date(2026, 4, 21)),
        ("en el dia 3 de junio", "es", date(2026, 6, 3)),
        ("en el 3 de junio", "es", date(2026, 6, 3)),
        ("in June 3rd", "en", date(2026, 6, 3)),
        ("on June the 3rd", "en", date(2026, 6, 3)),
        ("MAY 3", "en", date(2026, 5, 3)),
        ("mar 3", "en", date(2026, 3, 3)),
    ],
)
def test_a_month_and_day_phrase_without_a_year_resolves_as_a_partial_date(
    expression: str, language: Lang, expected_date: date
) -> None:
    result = resolve(expression, language=language, reference_date=_REFERENCE_DATE)

    assert result == (expected_date, DateSource.PARTIAL)


@pytest.mark.parametrize(
    ("expression", "language", "expected_date"),
    [
        ("June 3rd, 2025", "en", date(2025, 6, 3)),
        ("3 June 2025", "en", date(2025, 6, 3)),
        ("el 3 de junio de 2025", "es", date(2025, 6, 3)),
        ("3 de junho de 2025", "pt", date(2025, 6, 3)),
    ],
)
def test_a_month_and_day_phrase_with_a_year_resolves_as_an_absolute_date(
    expression: str, language: Lang, expected_date: date
) -> None:
    result = resolve(expression, language=language, reference_date=_REFERENCE_DATE)

    assert result == (expected_date, DateSource.ABSOLUTE)


def test_a_month_and_day_phrase_later_in_the_year_names_the_previous_year() -> None:
    """A customer cannot have transacted in the future, so "December 25" spoken in June is the
    December before."""
    result = resolve("December 25", language="en", reference_date=_REFERENCE_DATE)

    assert result == (date(2025, 12, 25), DateSource.PARTIAL)


@pytest.mark.parametrize(
    ("expression", "language"),
    [
        ("June 31", "en"),
        ("31 de abril", "es"),
        ("30 de fevereiro", "pt"),
        ("June 0", "en"),
        ("32 de maio", "pt"),
    ],
)
def test_a_month_and_day_phrase_naming_a_day_that_does_not_exist_resolves_to_nothing(
    expression: str, language: Lang
) -> None:
    assert resolve(expression, language=language, reference_date=_REFERENCE_DATE) is None


@pytest.mark.parametrize(
    ("expression", "language"),
    [
        ("June 3 2025 5", "en"),
        ("June 3 0025", "en"),
        ("June 3 1850", "en"),
        ("3 de marzo de 0026", "es"),
        ("el 3 de junio, creo", "es"),
        ("I may 3", "en"),
        ("21 de abril", "en"),
        ("3 de mar", "es"),
    ],
)
def test_a_phrase_that_is_not_a_month_and_day_resolves_to_nothing(
    expression: str, language: Lang
) -> None:
    """Only the whole expression is matched: trailing words, a year outside the plausible range
    and a month name from another language are not read as a date."""
    assert resolve(expression, language=language, reference_date=_REFERENCE_DATE) is None


def test_a_month_and_day_phrase_in_january_names_the_previous_year_for_a_later_month() -> None:
    result = resolve("3 de dezembro", language="pt", reference_date=date(2026, 1, 10))

    assert result == (date(2025, 12, 3), DateSource.PARTIAL)


def test_the_leap_day_resolves_only_in_a_year_that_has_one() -> None:
    """With no year stated, the reference date's own year is used: in a year with no 29 February
    the phrase names no date rather than the one in an earlier leap year."""
    assert resolve("Feb 29", language="en", reference_date=date(2026, 6, 18)) is None
    assert resolve("Feb 29", language="en", reference_date=date(2024, 6, 18)) == (
        date(2024, 2, 29),
        DateSource.PARTIAL,
    )
