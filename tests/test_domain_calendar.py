"""
Domain Calendar Tests
=======================

Component: ``app.domain.calendar``. Hermetic and pure: no clock, database or environment is
touched except the fake real-time source a test injects.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.calendar import (
    BANK_ZONE,
    DateOrigin,
    DomainCalendar,
    DomainCalendarError,
    resolve_domain_calendar,
)


def test_an_explicit_iso_date_wins_over_everything_else() -> None:
    """The setting, when given as a date, is used exactly as given, regardless of the seed."""
    calendar = resolve_domain_calendar("2026-06-18", seed_date=date(2020, 1, 1))

    assert calendar == DomainCalendar(date(2026, 6, 18), DateOrigin.SETTING)


def test_the_literal_system_selects_the_real_date_in_the_bank_zone() -> None:
    """ "system" ignores the seed and uses the injected clock, converted to America/Bogota."""
    # 03:00 UTC is still the previous day at UTC-5 (22:00 the day before)
    fake_now = lambda: datetime(2026, 6, 19, 3, 0, tzinfo=UTC)  # noqa: E731

    calendar = resolve_domain_calendar("system", seed_date=date(2020, 1, 1), now=fake_now)

    assert calendar == DomainCalendar(date(2026, 6, 18), DateOrigin.SYSTEM)


def test_the_system_keyword_is_case_insensitive_and_may_carry_whitespace() -> None:
    """Configuration is typed by hand; a stray case or space should not refuse to start."""
    fake_now = lambda: datetime(2026, 6, 18, 12, 0, tzinfo=UTC)  # noqa: E731

    calendar = resolve_domain_calendar("  System  ", seed_date=None, now=fake_now)

    assert calendar.origin is DateOrigin.SYSTEM


def test_the_seed_date_is_used_when_the_setting_is_absent() -> None:
    """No override: the reference date is the seed's own, marked as such."""
    calendar = resolve_domain_calendar(None, seed_date=date(2026, 6, 18))

    assert calendar == DomainCalendar(date(2026, 6, 18), DateOrigin.SEED)


@pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "whitespace"])
def test_a_blank_setting_is_treated_as_absent(blank: str) -> None:
    """The committed template leaves the setting empty; that must fall through to the seed."""
    calendar = resolve_domain_calendar(blank, seed_date=date(2026, 6, 18))

    assert calendar.origin is DateOrigin.SEED


def test_refuses_to_start_when_no_source_resolves() -> None:
    """No setting and no seed: the service must not guess the real date instead."""
    with pytest.raises(DomainCalendarError, match="No domain date resolves"):
        resolve_domain_calendar(None, seed_date=None)


def test_an_invalid_setting_is_refused_not_silently_ignored() -> None:
    """A typo in DATA_AS_OF_DATE must fail loudly, never fall back to the seed unnoticed."""
    with pytest.raises(DomainCalendarError, match="DATA_AS_OF_DATE"):
        resolve_domain_calendar("not-a-date", seed_date=date(2026, 6, 18))


def test_the_bank_zone_is_a_fixed_utc_minus_five() -> None:
    """No time-zone database is needed: Bogotá has had no daylight saving time since 1993."""
    assert BANK_ZONE.utcoffset(None).total_seconds() == -5 * 3600


def test_a_domain_calendar_is_immutable() -> None:
    """A resolved calendar cannot be edited after the fact."""
    calendar = DomainCalendar(date(2026, 6, 18), DateOrigin.SEED)

    with pytest.raises(AttributeError):
        calendar.reference_date = date(2020, 1, 1)  # type: ignore[misc]
