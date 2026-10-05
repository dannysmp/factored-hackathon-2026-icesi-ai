"""
Transaction Date Expression Resolution
========================================

Overview
--------
Resolves the customer's own words for when a transaction happened ("ayer", "el lunes", "dia 3",
"3 de junio", "03/04") into an absolute date and the ``DateSource`` that names how it was
expressed. Purely deterministic: no model call, no wall clock — the reference date is always the
caller's own input, the domain calendar's reference date, never ``date.today()``.

Scope
-----
In: matching a closed, curated vocabulary of relative day terms, weekday names and day-of-month
phrases and month-and-day phrases ("June 3rd", "3 de junio", "21 de abril") per language, and a
numeric day-first date pattern; resolving each against the reference date the caller supplies.
Out: recognizing that a message mentions a date at all (the model's own job, recorded as
``date_expression``); showing a resolved date back to the customer in words before it is used,
which this module does not do.

Design Principles
------------------
- A transaction date is never resolved into the future relative to the reference date: a weekday
  name or a day-of-month resolves to the most recent occurrence on or before it, matching the
  domain fact that a dispute is always about a transaction already in the past.
- An expression this table does not recognize resolves to ``None``, the same "nothing stated"
  outcome as a message with no date at all — an unrecognized phrase is never guessed at.
- A curated per-language table, the same shape ``app.retrieval.lexical``'s own stopword lists use:
  additive, reversible, and deliberately narrow rather than a general-purpose date parser. Vague
  ranges ("semana pasada", "last week") are deliberately left unresolved: picking one specific day
  out of a stated week would be inventing a fact the customer did not give, the same principle
  ``prompts/nlu_v1.yaml`` already states for the model itself.

Runtime Contract
----------------
``resolve(expression, *, language, reference_date) -> tuple[date, DateSource] | None``. A numeric
date (``dd/mm`` or ``dd/mm/yyyy``) resolves with any ``language``, including ``None``; every other
form needs the language to pick its vocabulary. ``DateSource.RELATIVE`` is a relative day term or a
weekday, ``DateSource.PARTIAL`` a day of the month, ``DateSource.NUMERIC`` a numeric date.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

from contracts.service_v1.envelope import DateSource, Lang

# Weekday index, Python's own convention (0=Monday .. 6=Sunday), per language's accent-folded,
# lowercased name. Long and short Portuguese weekday forms both map to the same index.
_WEEKDAYS: dict[Lang, dict[str, int]] = {
    "es": {
        "lunes": 0,
        "martes": 1,
        "miercoles": 2,
        "jueves": 3,
        "viernes": 4,
        "sabado": 5,
        "domingo": 6,
    },
    "pt": {
        "segunda": 0,
        "segunda-feira": 0,
        "terca": 1,
        "terca-feira": 1,
        "quarta": 2,
        "quarta-feira": 2,
        "quinta": 3,
        "quinta-feira": 3,
        "sexta": 4,
        "sexta-feira": 4,
        "sabado": 5,
        "domingo": 6,
    },
    "en": {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    },
}

# Days back from the reference date, per language's accent-folded, lowercased phrase.
_RELATIVE_DAYS: dict[Lang, dict[str, int]] = {
    "es": {"hoy": 0, "ayer": 1, "anteayer": 2, "antes de ayer": 2},
    "pt": {"hoje": 0, "ontem": 1, "anteontem": 2},
    "en": {"today": 0, "yesterday": 1, "the day before yesterday": 2},
}

# A day-of-month phrase, per language: "el 3"/"el dia 3" (es), "dia 3" (pt), "the 3rd" (en).
_DAY_OF_MONTH: dict[Lang, re.Pattern[str]] = {
    "es": re.compile(r"^el\s+(?:dia\s+)?(\d{1,2})$"),
    "pt": re.compile(r"^dia\s+(\d{1,2})$"),
    "en": re.compile(r"^(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)$"),
}

# Month number per language's accent-folded, lowercased name; abbreviations a customer commonly
# types are included alongside the full names.
_MONTHS: dict[Lang, dict[str, int]] = {
    "es": {
        "enero": 1,
        "febrero": 2,
        "marzo": 3,
        "abril": 4,
        "mayo": 5,
        "junio": 6,
        "julio": 7,
        "agosto": 8,
        "septiembre": 9,
        "setiembre": 9,
        "octubre": 10,
        "noviembre": 11,
        "diciembre": 12,
    },
    "pt": {
        "janeiro": 1,
        "fevereiro": 2,
        "marco": 3,
        "abril": 4,
        "maio": 5,
        "junho": 6,
        "julho": 7,
        "agosto": 8,
        "setembro": 9,
        "outubro": 10,
        "novembro": 11,
        "dezembro": 12,
    },
    "en": {
        "january": 1,
        "jan": 1,
        "february": 2,
        "feb": 2,
        "march": 3,
        "mar": 3,
        "april": 4,
        "apr": 4,
        "may": 5,
        "june": 6,
        "jun": 6,
        "july": 7,
        "jul": 7,
        "august": 8,
        "aug": 8,
        "september": 9,
        "sept": 9,
        "sep": 9,
        "october": 10,
        "oct": 10,
        "november": 11,
        "nov": 11,
        "december": 12,
        "dec": 12,
    },
}


def _month_alternation(language: Lang) -> str:
    """The language's month names as a regular-expression alternation, longest first so a full
    name is never cut short by one of its own abbreviations."""
    return "|".join(sorted(_MONTHS[language], key=len, reverse=True))


# A month-and-day phrase, per language, with the day and month as named groups and an optional
# four-digit year: "3 de junio" / "el 3 de junio de 2026" (es), "dia 21 de abril" / "no dia 21 de
# abril" (pt), "June 3rd" / "3rd of June" / "on the 3rd of June, 2026" (en). A leading preposition
# and article are accepted because the model may report the customer's phrase as spoken.
_YEAR = r"(?P<year>(?:19|20)\d{2})"
_MONTH_DAY: dict[Lang, tuple[re.Pattern[str], ...]] = {
    "es": (
        re.compile(
            r"^(?:(?:en\s+)?el\s+)?(?:dia\s+)?(?P<day>\d{1,2})\s+de\s+"
            rf"(?P<month>{_month_alternation('es')})(?:\s+(?:de|del)\s+{_YEAR})?$"
        ),
    ),
    "pt": (
        re.compile(
            r"^(?:(?:no|em|em\s+o|o)\s+)?(?:dia\s+)?(?P<day>\d{1,2})\s+de\s+"
            rf"(?P<month>{_month_alternation('pt')})(?:\s+de\s+{_YEAR})?$"
        ),
    ),
    "en": (
        re.compile(
            r"^(?:(?:on|in)\s+)?(?:the\s+)?"
            rf"(?P<month>{_month_alternation('en')})\.?\s+(?:the\s+)?(?P<day>\d{{1,2}})"
            rf"(?:st|nd|rd|th)?(?:,?\s+{_YEAR})?$"
        ),
        re.compile(
            r"^(?:(?:on|in)\s+)?(?:the\s+)?(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?"
            rf"(?P<month>{_month_alternation('en')})\.?(?:,?\s+{_YEAR})?$"
        ),
    ),
}

# Day first, in every language — never the customer's own language's usual convention (English
# included). An optional two- or four-digit year; without one, the reference date's own year.
_NUMERIC = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$")

_MAX_MONTHS_BACK = 4  # Bounded search for a day-of-month that doesn't exist in every month.
_TWO_DIGIT_YEAR_CUTOFF = 100  # A numeric date's own year below this is "26", not "2026".


def _fold(text: str) -> str:
    """Lowercased, accent-stripped, whitespace-collapsed — matching how the closed vocabularies
    below are written, regardless of how the model capitalizes or accents what it reports."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )
    return " ".join(stripped.split())


def _most_recent_weekday(reference_date: date, target_weekday: int) -> date:
    """The most recent date on or before ``reference_date`` that falls on ``target_weekday``."""
    days_back = (reference_date.weekday() - target_weekday) % 7
    return reference_date - timedelta(days=days_back)


def _most_recent_day_of_month(reference_date: date, day: int) -> date | None:
    """The most recent date on or before ``reference_date`` whose day-of-month is ``day``.

    Walks back a bounded number of months (some months don't have a 29th-31st); gives up rather
    than guess a different day if none of them do.
    """
    year, month = reference_date.year, reference_date.month
    for _ in range(_MAX_MONTHS_BACK):
        candidate: date | None
        try:
            candidate = date(year, month, day)
        except ValueError:
            candidate = None
        if candidate is not None and candidate <= reference_date:
            return candidate
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return None


def _resolve_year(day: int, month: int, year: int | None, reference_date: date) -> date | None:
    """A numeric date's own year, when it has one; otherwise the reference date's year, or the
    year before it when that combination would fall after the reference date — a transaction date
    is always in the past. An explicit year that is still in the future is rejected outright:
    unlike the no-year case, there is no sensible earlier year to fall back to when the customer
    stated one themselves."""
    if year is not None:
        if year < _TWO_DIGIT_YEAR_CUTOFF:
            year += 2000
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        return candidate if candidate <= reference_date else None
    try:
        candidate = date(reference_date.year, month, day)
    except ValueError:
        return None
    if candidate > reference_date:
        try:
            return date(reference_date.year - 1, month, day)
        except ValueError:
            return None
    return candidate


def _resolve_day_of_month(
    folded: str, language: Lang, reference_date: date
) -> tuple[date, DateSource] | None:
    """The date a bare day-of-month phrase names, as its most recent occurrence."""
    day_of_month = _DAY_OF_MONTH[language].match(folded)
    if day_of_month is None:
        return None
    resolved = _most_recent_day_of_month(reference_date, int(day_of_month.group(1)))
    return (resolved, DateSource.PARTIAL) if resolved is not None else None


def _resolve_month_day(
    folded: str, language: Lang, reference_date: date
) -> tuple[date, DateSource] | None:
    """The date a month-and-day phrase names, with its year when the phrase states one."""
    for pattern in _MONTH_DAY[language]:
        month_day = pattern.match(folded)
        if month_day is None:
            continue
        year_text = month_day.group("year")
        resolved = _resolve_year(
            int(month_day.group("day")),
            _MONTHS[language][month_day.group("month")],
            int(year_text) if year_text is not None else None,
            reference_date,
        )
        if resolved is None:
            return None
        return resolved, DateSource.ABSOLUTE if year_text is not None else DateSource.PARTIAL
    return None


def resolve(
    expression: str, *, language: Lang | None, reference_date: date
) -> tuple[date, DateSource] | None:
    """The date ``expression`` names, resolved against ``reference_date``, and how it was
    expressed — or ``None`` when it names no date this table recognizes.

    Parameters
    ----------
    expression : str
        The customer's own words for the date, as the understanding step reported them.
    language : Lang | None
        The conversation's language, which selects the vocabulary; ``None`` leaves only the numeric
        form resolvable.
    reference_date : date
        The domain calendar's reference date; every result is on or before it.

    Returns
    -------
    tuple[date, DateSource] | None
        The resolved date with how it was expressed, or ``None`` for an unrecognized phrase, a date
        that does not exist, or a numeric date with an explicit year after ``reference_date``.
    """
    numeric = _NUMERIC.match(expression.strip())
    if numeric is not None:
        day, month, year_text = numeric.group(1), numeric.group(2), numeric.group(3)
        resolved = _resolve_year(
            int(day), int(month), int(year_text) if year_text is not None else None, reference_date
        )
        return (resolved, DateSource.NUMERIC) if resolved is not None else None

    if language is None:
        return None

    folded = _fold(expression)

    relative_days = _RELATIVE_DAYS[language].get(folded)
    if relative_days is not None:
        return reference_date - timedelta(days=relative_days), DateSource.RELATIVE

    weekday = _WEEKDAYS[language].get(folded)
    if weekday is not None:
        return _most_recent_weekday(reference_date, weekday), DateSource.RELATIVE

    return _resolve_month_day(folded, language, reference_date) or _resolve_day_of_month(
        folded, language, reference_date
    )
