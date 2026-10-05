"""
Domain Calendar
================

Overview
--------
The one date policy decisions, cases and audit records use for "today": a domain date
resolved once at start-up, never the real clock. Two clocks exist on purpose — the domain date for
policy and customer-visible dates, the real UTC instant for everything a timestamp of record needs
— and this module owns only the first.

Scope
-----
In: resolving the reference date from ``DATA_AS_OF_DATE``, the loaded seed, or the real date in
the bank's own zone, and refusing when none of those answers.
Out: reading ``DATABASE_URL`` or querying Postgres (the caller supplies the seed's date, already
read); the real UTC clock, used elsewhere for timestamps of record.

Design Principles
-----------------
- Built once at start-up, from a dedicated setting independent of the environment name. There is
  no per-request override: no header, query field or body field changes the domain date.
- An explicit ISO date wins; the literal ``"system"`` selects the real date in the bank zone;
  otherwise the seed's own reference date; if none of these resolves, the caller must refuse to
  start rather than fall back silently to the real date.
- The bank's operating zone, America/Bogota, is a fixed UTC-5 offset: Bogotá has had no daylight
  saving time since 1993, so no time-zone database is needed.
- The evaluation harness builds its own ``DomainCalendar`` in-process, directly, for fixed dates;
  this module's resolution logic is not the only way to construct one.

Runtime Contract
-----------------
``DomainCalendar(reference_date, origin)``; ``DateOrigin`` (``setting``, ``seed``, ``system``);
``resolve_domain_calendar(setting, seed_date, now=...) -> DomainCalendar`` raises
``DomainCalendarError`` when none of the three sources resolves.

Limitations
-----------
Nothing here enforces that session, audit or logging code never reads this module, or that policy
code never reads the real clock instead; that separation is a fitness test over the import graph,
not a runtime check this module can make of its own callers.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Callable  # Type of the injected real-time source
from dataclasses import dataclass  # Immutable calendar and its origin
from datetime import UTC, date, datetime, timedelta, timezone  # Dates, real clock, fixed zone
from enum import StrEnum  # Closed set of where the reference date came from

# The bank's operating zone, America/Bogota: a fixed UTC-5 offset.
BANK_ZONE = timezone(timedelta(hours=-5))
# Setting value that selects the real current date in the bank zone.
SYSTEM_KEYWORD = "system"


class DateOrigin(StrEnum):
    """Where the domain date came from: an explicit setting, the loaded seed or the real date."""

    SETTING = "setting"
    SEED = "seed"
    SYSTEM = "system"


class DomainCalendarError(Exception):
    """No domain date resolves; the caller must refuse to start, not fall back to the real date."""


@dataclass(frozen=True, slots=True)
class DomainCalendar:
    """The resolved reference date and where it came from.

    Immutable. ``reference_date`` is the date policy decisions and customer-visible dates treat as
    "today"; ``origin`` records which source supplied it.
    """

    reference_date: date
    origin: DateOrigin


def utc_now() -> datetime:
    """The current time in UTC; the production real-time source, replaced by tests."""
    return datetime.now(UTC)


def resolve_domain_calendar(
    setting: str | None,
    seed_date: date | None,
    *,
    now: Callable[[], datetime] = utc_now,
) -> DomainCalendar:
    """Resolve the domain date: an explicit setting, then the seed, then refusal.

    A non-blank ``setting`` decides alone: the literal ``"system"`` (any case) gives the current
    date in ``BANK_ZONE`` and an ISO date is used as written. A blank or missing setting falls
    back to ``seed_date``.

    Parameters
    ----------
    setting : str | None
        ``DATA_AS_OF_DATE`` as given: an ISO date, the literal ``"system"``, or ``None``/blank.
    seed_date : date | None
        ``ops_meta.data_as_of`` from the loaded seed, already read by the caller; ``None`` when
        the seed has not been loaded or could not be read.
    now : Callable[[], datetime]
        Source of the real instant, for the ``"system"`` setting; tests inject their own.

    Returns
    -------
    DomainCalendar
        The reference date and the origin that supplied it.

    Raises
    ------
    DomainCalendarError
        ``setting`` is neither a valid ISO date nor ``"system"``, or (when ``setting`` is blank)
        ``seed_date`` is also ``None``: no source resolves a date.
    """
    trimmed = (setting or "").strip()
    if trimmed:
        if trimmed.lower() == SYSTEM_KEYWORD:
            return DomainCalendar(now().astimezone(BANK_ZONE).date(), DateOrigin.SYSTEM)
        try:
            explicit = date.fromisoformat(trimmed)
        except ValueError:
            raise DomainCalendarError(
                f"DATA_AS_OF_DATE is neither an ISO date nor {SYSTEM_KEYWORD!r}: {setting!r}"
            ) from None
        return DomainCalendar(explicit, DateOrigin.SETTING)
    if seed_date is not None:
        return DomainCalendar(seed_date, DateOrigin.SEED)
    raise DomainCalendarError(
        "No domain date resolves: set DATA_AS_OF_DATE or load the seed (ops_meta.data_as_of)"
    )
