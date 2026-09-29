"""
Transaction Date Expression Resolution
========================================

Overview
--------
Resolves the customer's own words for when a transaction happened ("ayer", "el lunes", "dia 3",
"03/04") into an absolute date and the ``DateSource`` that names how it was expressed (AC-E5-16).
Purely deterministic: no model call, no wall clock — the reference date is always the caller's own
input, the domain calendar's reference date, never ``date.today()``.

Scope
-----
In: matching a closed, curated vocabulary of relative day terms, weekday names and day-of-month
phrases per language, and a numeric day-first date pattern; resolving each against the reference
date the caller supplies.
Out: recognizing that a message mentions a date at all (the model's own job, recorded as
``date_expression``); confirming a resolved date in words before it is used (a later, separate
controller-slice concern per AC-E5-16 and issue #106's own stated scope).

Design Principles
------------------
- A transaction date is never resolved into the future relative to the reference date: a weekday
  name or a day-of-month resolves to the most recent occurrence on or before it, matching the
  domain fact that a dispute is always about a transaction already in the past.
- An expression this table does not recognize resolves to ``None``, exactly the same "nothing
  stated" outcome as before this module existed — an unrecognized phrase is never guessed at.
- A curated per-language table, the same shape ``app.retrieval.lexical``'s own stopword lists use:
  additive, reversible, and deliberately narrow rather than a general-purpose date parser. Vague
  ranges ("semana pasada", "last week") are deliberately left unresolved: picking one specific day
  out of a stated week would be inventing a fact the customer did not give, the same principle
  ``prompts/nlu_v1.yaml`` already states for the model itself.
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

# Day first, in every language, per AC-E5-16 — never the customer's own language's usual
# convention. An optional two- or four-digit year; without one, the reference date's own year.
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


def resolve(
    expression: str, *, language: Lang | None, reference_date: date
) -> tuple[date, DateSource] | None:
    """The date ``expression`` names, resolved against ``reference_date``, and how it was
    expressed — or ``None`` when it names no date this table recognizes.
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

    day_of_month = _DAY_OF_MONTH[language].match(folded)
    if day_of_month is not None:
        resolved = _most_recent_day_of_month(reference_date, int(day_of_month.group(1)))
        return (resolved, DateSource.PARTIAL) if resolved is not None else None

    return None
