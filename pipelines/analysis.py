"""
Workflow Analysis
=================

Overview
--------
Turns the dispute demand marts into ``reports/workflow-analysis.md``: how much of the bank's
service demand is about disputed transactions, how those cases are resolved today, how customers
feel about the contacts, what handling them costs and which outcomes an automated workflow
should reach. Everything in the report is computed here from the marts and the assumptions
file, so it regenerates with one command and no figure is typed by hand.

Scope
-----
In: reading the marts and the assumptions, computing the figures, rendering the report and the
command line ``python -m pipelines.analysis``.
Out: building the marts (``pipelines.gold``) and cleaning the data (``pipelines.silver``).

Design Principles
-----------------
- Rendering is a pure function of the marts and the assumptions: no clock, no filesystem, so
  the same inputs always give the same text.
- Anything not measured in the data comes from ``analysis_assumptions.toml`` and is shown with
  its low, base and high value.
- Only aggregates appear; no customer, account or free-text value can reach the report.

Runtime Contract
----------------
``load_assumptions(path) -> Assumptions``
``render_workflow_analysis(marts, assumptions, manifest) -> str``
``main(argv) -> int``

Limitations
-----------
The dispute proxy and the missing link between contacts and cases (see the report's scope
section) bound every figure; they are restated in the report itself.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command line
import logging  # Progress events
import os  # Atomic report replacement
import tomllib  # Reading the assumptions file
from collections.abc import Mapping, Sequence  # Types of the marts and rows
from dataclasses import dataclass  # Immutable assumption objects
from pathlib import Path  # Locations
from typing import Any  # Mart rows

# Local modules
from pipelines.gold import (  # Marts and the definitions the report quotes
    ADJACENT_CATEGORY,
    ADJACENT_SUBCATEGORY,
    DISPUTE_CATEGORY,
    DISPUTE_SUBCATEGORY,
    MART_NAMES,
    UNMATCHED,
    UNSPECIFIED,
    build_marts,
    read_mart,
)
from pipelines.silver import git_version  # Same code-version rule as the cleaning stage

logger = logging.getLogger(__name__)

DEFAULT_ASSUMPTIONS = Path(__file__).with_name("analysis_assumptions.toml")
DIGEST_DIGITS = 12

Row = dict[str, Any]
Marts = Mapping[str, Sequence[Row]]


# -----------------------------------------------------------------------------
# Assumptions
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Range:
    """A planning assumption with a low, base and high value."""

    low: float
    base: float
    high: float


@dataclass(frozen=True, slots=True)
class Targets:
    """Outcomes proposed for the automated workflow."""

    safe_automated_resolution_rate: float
    sla_breach_rate_max: float
    median_days_to_resolution_max: float
    repeat_complainer_rate_max: float
    unsafe_action_rate_max: float


@dataclass(frozen=True, slots=True)
class Assumptions:
    """Everything the report takes from outside the data."""

    agent_hour_usd: Range
    contacts_per_dispute: Range
    handling_reason_category: str
    targets: Targets


def _range(table: Mapping[str, Any], key: str) -> Range:
    """Read a low/base/high assumption, checking that it is ordered and positive."""
    values = table[key]
    parsed = Range(float(values["low"]), float(values["base"]), float(values["high"]))
    if not 0 < parsed.low <= parsed.base <= parsed.high:
        raise ValueError(f"assumption {key} must satisfy 0 < low <= base <= high")
    return parsed


def load_assumptions(path: Path = DEFAULT_ASSUMPTIONS) -> Assumptions:
    """Read and validate the assumptions file.

    Raises
    ------
    ValueError
        When a value is missing, out of order or outside its allowed range.
    OSError
        When the file cannot be read.
    """
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    try:
        cost = document["cost"]
        targets = document["targets"]
        parsed = Assumptions(
            agent_hour_usd=_range(cost, "agent_hour_usd"),
            contacts_per_dispute=_range(cost, "contacts_per_dispute"),
            handling_reason_category=str(cost["handling_reason_category"]),
            targets=Targets(
                safe_automated_resolution_rate=float(targets["safe_automated_resolution_rate"]),
                sla_breach_rate_max=float(targets["sla_breach_rate_max"]),
                median_days_to_resolution_max=float(targets["median_days_to_resolution_max"]),
                repeat_complainer_rate_max=float(targets["repeat_complainer_rate_max"]),
                unsafe_action_rate_max=float(targets["unsafe_action_rate_max"]),
            ),
        )
    except KeyError as missing:
        raise ValueError(f"assumption {missing} is missing from {path.name}") from None
    rates = (
        parsed.targets.safe_automated_resolution_rate,
        parsed.targets.sla_breach_rate_max,
        parsed.targets.repeat_complainer_rate_max,
        parsed.targets.unsafe_action_rate_max,
    )
    if not all(0 <= rate <= 1 for rate in rates):
        raise ValueError("target rates must lie between 0 and 1")
    if parsed.targets.median_days_to_resolution_max < 0:
        raise ValueError("the median-days target must not be negative")
    return parsed


# -----------------------------------------------------------------------------
# Formatting
# -----------------------------------------------------------------------------


def _count(value: float) -> str:
    """Integer with thousands separators."""
    return f"{round(value):,}"


def _ratio(numerator: float, denominator: float) -> float | None:
    """Quotient, or None when the denominator is zero."""
    return numerator / denominator if denominator else None


def _pct(value: float | None) -> str:
    """Percentage with one decimal, or ``n/a`` when undefined."""
    return "n/a" if value is None else f"{value * 100:.1f} %"


def _number(value: float | None, digits: int = 1) -> str:
    """Decimal number, or ``n/a`` when undefined."""
    if value is None:
        return "n/a"
    # A value that rounds to zero is shown without a sign ("-0.00" would suggest a direction)
    return f"{0.0 if round(value, digits) == 0 else value:,.{digits}f}"


def _usd(value: float | None) -> str:
    """Amount in US dollars with two decimals, or ``n/a``."""
    return "n/a" if value is None else f"USD {value:,.2f}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Markdown table; an empty row list renders a single 'none' row."""
    body = rows or [["none", *[""] * (len(headers) - 1)]]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _total(rows: Sequence[Row], column: str) -> float:
    """Sum of a column, treating missing values as zero."""
    return float(sum(row[column] or 0 for row in rows))


# -----------------------------------------------------------------------------
# Figures
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContactProfile:
    """Handling figures of the contacts of one reason category."""

    category: str
    interactions: int
    handle_minutes: float | None
    wait_minutes: float | None
    resolved_rate: float | None
    escalated_rate: float | None
    followup_rate: float | None
    negative_rate: float | None
    neutral_rate: float | None
    mean_sentiment: float | None
    survey_score: float | None


def _contact_profiles(marts: Marts) -> list[ContactProfile]:
    """One profile per reason category, ordered by name."""
    demand = marts["contact_demand_monthly"]
    surveys = {row["reason_category"]: row for row in marts["contact_satisfaction"]}
    categories = sorted({row["reason_category"] for row in demand if row["reason_category"]})
    profiles = []
    for category in categories:
        rows = [row for row in demand if row["reason_category"] == category]
        interactions = _total(rows, "interactions")
        with_duration = _total(rows, "interactions_with_duration")
        with_wait = _total(rows, "interactions_with_wait")
        with_score = _total(rows, "interactions_with_score")
        duration_mean = _ratio(_total(rows, "duration_seconds_sum"), with_duration)
        wait_mean = _ratio(_total(rows, "wait_seconds_sum"), with_wait)
        survey = surveys.get(category)
        profiles.append(
            ContactProfile(
                category=category,
                interactions=round(interactions),
                handle_minutes=None if duration_mean is None else duration_mean / 60,
                wait_minutes=None if wait_mean is None else wait_mean / 60,
                resolved_rate=_ratio(_total(rows, "resolved_interactions"), interactions),
                escalated_rate=_ratio(_total(rows, "escalated_interactions"), interactions),
                followup_rate=_ratio(_total(rows, "followup_interactions"), interactions),
                negative_rate=_ratio(_total(rows, "negative_interactions"), interactions),
                neutral_rate=_ratio(_total(rows, "neutral_interactions"), interactions),
                mean_sentiment=_ratio(_total(rows, "sentiment_score_sum"), with_score),
                survey_score=(
                    _ratio(float(survey["score_sum"] or 0), float(survey["surveys_with_score"]))
                    if survey
                    else None
                ),
            )
        )
    return profiles


def _dispute_totals(marts: Marts) -> dict[str, float]:
    """Totals over all dispute cases."""
    monthly = marts["dispute_cases_monthly"]
    return {
        "cases": _total(monthly, "cases"),
        "closed": _total(monthly, "closed_cases"),
        "escalated": _total(monthly, "escalated_cases"),
        "rejected": _total(monthly, "rejected_cases"),
        "sla_breached": _total(monthly, "sla_breached_cases"),
        "repeat": _total(monthly, "repeat_complainer_cases"),
        "first_response": _total(monthly, "first_response_cases"),
        "months": float(len(monthly)),
    }


def _resolution_days(marts: Marts) -> tuple[float | None, float | None]:
    """Median and 90th percentile of days to resolution over resolved and closed cases."""
    (overall,) = marts["dispute_resolution_overall"]
    if not overall["cases_with_days"]:
        return None, None
    return float(overall["median_days"]), float(overall["p90_days"])


def _handling_cost(
    assumptions: Assumptions, profile: ContactProfile | None
) -> dict[str, float | None]:
    """Cost of handling one dispute at the low, base and high assumption."""
    if profile is None or profile.handle_minutes is None:
        return {"low": None, "base": None, "high": None}
    hours = profile.handle_minutes / 60
    return {
        "low": (hours * assumptions.agent_hour_usd.low * assumptions.contacts_per_dispute.low),
        "base": (hours * assumptions.agent_hour_usd.base * assumptions.contacts_per_dispute.base),
        "high": (hours * assumptions.agent_hour_usd.high * assumptions.contacts_per_dispute.high),
    }


# -----------------------------------------------------------------------------
# Report
# -----------------------------------------------------------------------------


def _scope_section(marts: Marts) -> str:
    """Definitions and the limits of the source that bound every figure."""
    return "\n".join(
        [
            "## 1. Scope, definitions and limits of the source",
            "",
            f"- **Dispute case:** a complaint of the category `{DISPUTE_CATEGORY}` whose "
            f"subcategory is `{DISPUTE_SUBCATEGORY}` (an unrecognised charge). The source has no "
            "dispute flag, so this is a proxy. Undue charges (category "
            f"`{ADJACENT_CATEGORY}`, subcategory `{ADJACENT_SUBCATEGORY}`) are a neighbouring kind "
            "of dispute and are sized in section 2 as a sensitivity, together with the "
            f"`{DISPUTE_CATEGORY}` complaints that carry no subcategory. Duplicate charges cannot "
            "be told apart.",
            "- **Contacts cannot be tied to cases:** the link from a complaint to the contact "
            "that originated it is empty in the source, and the contact reason repeats the "
            "six broad categories. Contact figures describe reason categories, not disputes; "
            "the cost model states how it bridges the gap.",
            "- **Statuses:** only `Resolved` and `Closed` cases carry days to resolution; open, "
            "in-process, escalated and rejected cases do not.",
            "- **Amounts** are reported per currency and never added across currencies; many "
            "cases carry no currency.",
            f"- **Marts read:** {', '.join(f'`{name}`' for name in MART_NAMES)}.",
        ]
    )


def _demand_section(marts: Marts, profiles: list[ContactProfile]) -> str:
    """Dispute demand and its place among all complaints and contacts."""
    totals = _dispute_totals(marts)
    mix = marts["complaint_category_mix"]
    all_complaints = _total(mix, "cases")
    transactions = _total([row for row in mix if row["category"] == DISPUTE_CATEGORY], "cases")
    monthly = marts["dispute_cases_monthly"]
    per_year: dict[int, float] = {}
    for row in monthly:
        per_year[row["month"].year] = per_year.get(row["month"].year, 0) + row["cases"]
    interactions = sum(profile.interactions for profile in profiles)
    first = monthly[0]["month"].strftime("%Y-%m") if monthly else "n/a"
    last = monthly[-1]["month"].strftime("%Y-%m") if monthly else "n/a"
    peak = max(monthly, key=lambda row: (row["cases"], row["month"]), default=None)
    lines = [
        "## 2. Demand",
        "",
        f"Dispute cases: **{_count(totals['cases'])}** between {first} and {last} "
        f"({_count(totals['months'])} months with cases), "
        f"{_number(_ratio(totals['cases'], totals['months']), 0)} per month on average"
        + (
            f"; the busiest month is {peak['month']:%Y-%m} with {_count(peak['cases'])}."
            if peak
            else "."
        ),
        "",
        f"- Share of all complaints: **{_pct(_ratio(totals['cases'], all_complaints))}** "
        f"({_count(totals['cases'])} of {_count(all_complaints)}).",
        f"- Share of `{DISPUTE_CATEGORY}` complaints: "
        f"**{_pct(_ratio(totals['cases'], transactions))}**.",
        "",
        "Dispute cases per calendar year (partial years cover only the months in the data):",
        "",
        _table(
            ["Year", "Cases"],
            [[str(year), _count(count)] for year, count in sorted(per_year.items())],
        ),
        "",
        f"Contacts by reason category ({_count(interactions)} in total):",
        "",
        _table(
            ["Reason category", "Contacts", "Share"],
            [
                [
                    profile.category,
                    _count(profile.interactions),
                    _pct(_ratio(profile.interactions, interactions)),
                ]
                for profile in sorted(profiles, key=lambda p: (-p.interactions, p.category))
            ],
        ),
        "",
        "Complaints by category:",
        "",
        _table(
            ["Category", "Cases", "Share"],
            [
                [
                    category,
                    _count(cases),
                    _pct(_ratio(cases, all_complaints)),
                ]
                for category, cases in sorted(
                    _group_total(mix, "category").items(), key=lambda item: (-item[1], item[0])
                )
            ],
        ),
        "",
        "Complaints by category and subcategory, with the role each plays in the dispute "
        "definition:",
        "",
        _table(
            ["Category", "Subcategory", "Cases", "Role"],
            [
                [
                    row["category"],
                    row["subcategory"],
                    _count(row["cases"]),
                    _dispute_role(row["category"], row["subcategory"]),
                ]
                for row in mix
            ],
        ),
        "",
        "Sensitivity of the dispute count to the definition:",
        "",
        _table(
            ["Definition", "Cases", "Share of complaints"],
            [
                [label, _count(cases), _pct(_ratio(cases, all_complaints))]
                for label, cases in _definition_sizes(mix)
            ],
        ),
    ]
    return "\n".join(lines)


def _dispute_role(category: str, subcategory: str) -> str:
    """Role of a complaint category and subcategory in the dispute definition."""
    if (category, subcategory) == (DISPUTE_CATEGORY, DISPUTE_SUBCATEGORY):
        return "primary dispute"
    if (category, subcategory) == (ADJACENT_CATEGORY, ADJACENT_SUBCATEGORY):
        return "adjacent (sensitivity)"
    if category == DISPUTE_CATEGORY and subcategory == UNSPECIFIED:
        return "unclassified (sensitivity)"
    return "not a dispute"


def _definition_sizes(mix: Sequence[Row]) -> list[tuple[str, float]]:
    """Cases under the primary definition and under each widening of it."""

    def cases(category: str, subcategory: str) -> float:
        return _total(
            [r for r in mix if (r["category"], r["subcategory"]) == (category, subcategory)],
            "cases",
        )

    primary = cases(DISPUTE_CATEGORY, DISPUTE_SUBCATEGORY)
    adjacent = cases(ADJACENT_CATEGORY, ADJACENT_SUBCATEGORY)
    unclassified = cases(DISPUTE_CATEGORY, UNSPECIFIED)
    return [
        (f"Primary: `{DISPUTE_CATEGORY}` / `{DISPUTE_SUBCATEGORY}`", primary),
        (
            f"Plus undue charges (`{ADJACENT_CATEGORY}` / `{ADJACENT_SUBCATEGORY}`)",
            primary + adjacent,
        ),
        (
            f"Plus `{DISPUTE_CATEGORY}` without a subcategory",
            primary + adjacent + unclassified,
        ),
    ]


def _group_total(rows: Sequence[Row], key: str) -> dict[str, float]:
    """Sum of ``cases`` per value of ``key``."""
    totals: dict[str, float] = {}
    for row in rows:
        totals[row[key]] = totals.get(row[key], 0) + row["cases"]
    return totals


def _resolution_section(marts: Marts) -> str:
    """How dispute cases end today: status, time, SLA and repeat complainers."""
    totals = _dispute_totals(marts)
    median, p90 = _resolution_days(marts)
    in_progress = totals["cases"] - totals["closed"] - totals["rejected"]
    status_rows = []
    for row in marts["dispute_resolution"]:
        status_rows.append(
            [
                row["status"],
                _count(row["cases"]),
                _pct(_ratio(row["cases"], totals["cases"])),
                _number(_ratio(row["days_sum"] or 0, row["cases_with_days"]))
                if row["cases_with_days"]
                else "n/a",
                _number(row["median_days"]) if row["cases_with_days"] else "n/a",
                _number(row["p90_days"]) if row["cases_with_days"] else "n/a",
                _pct(_ratio(row["sla_breached_cases"], row["cases"])),
            ]
        )
    currency_rows = [
        [
            row["currency"],
            _count(row["cases"]),
            _count(row["cases_with_amount"]),
            _number(_ratio(float(row["claimed_total"] or 0), row["cases_with_amount"]), 2)
            if row["cases_with_amount"]
            else "n/a",
        ]
        for row in marts["dispute_claims_by_currency"]
    ]
    return "\n".join(
        [
            "## 3. Resolution today",
            "",
            f"- Cases that reached `Resolved` or `Closed`: "
            f"**{_pct(_ratio(totals['closed'], totals['cases']))}**; still open, in process or "
            f"escalated: {_pct(_ratio(in_progress, totals['cases']))}; "
            f"rejected: {_pct(_ratio(totals['rejected'], totals['cases']))}.",
            f"- Days to resolution (resolved and closed cases): median **{_number(median)}**, "
            f"90th percentile {_number(p90)}.",
            f"- SLA breached: **{_pct(_ratio(totals['sla_breached'], totals['cases']))}** of "
            "cases.",
            f"- Repeat complainers: **{_pct(_ratio(totals['repeat'], totals['cases']))}** of "
            "cases.",
            f"- First response recorded: {_pct(_ratio(totals['first_response'], totals['cases']))} "
            "of cases.",
            "",
            _table(
                [
                    "Status",
                    "Cases",
                    "Share",
                    "Mean days",
                    "Median days",
                    "P90 days",
                    "SLA breached",
                ],
                status_rows,
            ),
            "",
            "Claimed amounts by currency (mean per case that states an amount):",
            "",
            _table(["Currency", "Cases", "With amount", "Mean claimed"], currency_rows),
        ]
    )


NEUTRAL_CAVEAT_SHARE = 0.95


def _sentiment_section(marts: Marts, profiles: list[ContactProfile]) -> str:
    """Sentiment and satisfaction of contacts by reason category."""
    unmatched = _total(
        [r for r in marts["contact_satisfaction"] if r["reason_category"] == UNMATCHED], "surveys"
    )
    caveats = [
        f"`{profile.category}` contacts are recorded as neutral in {_pct(profile.neutral_rate)} "
        "of cases, so their sentiment says little about how a customer feels about a dispute."
        for profile in profiles
        if profile.neutral_rate is not None and profile.neutral_rate >= NEUTRAL_CAVEAT_SHARE
    ]
    rows = [
        [
            profile.category,
            _number(profile.handle_minutes),
            _number(profile.wait_minutes),
            _pct(profile.negative_rate),
            _pct(profile.neutral_rate),
            _number(profile.mean_sentiment, 2),
            _number(profile.survey_score, 2),
            _pct(profile.resolved_rate),
            _pct(profile.escalated_rate),
            _pct(profile.followup_rate),
        ]
        for profile in profiles
    ]
    return "\n".join(
        [
            "## 4. Handling, sentiment, satisfaction and outcome of contacts",
            "",
            "Negative share is the share of contacts detected as `Negativo` or `Muy Negativo`; "
            "mean sentiment runs from -1 to 1; the survey score is the mean main score of the "
            "surveys tied to contacts of the category.",
            "",
            _table(
                [
                    "Reason category",
                    "Mean handling (min)",
                    "Mean wait (min)",
                    "Negative",
                    "Neutral",
                    "Mean sentiment",
                    "Survey score",
                    "Resolved",
                    "Escalated",
                    "Follow-up needed",
                ],
                rows,
            ),
            "",
            *caveats,
            f"Surveys that reference no contact ({_count(unmatched)}) are not attributed to a "
            "reason category.",
        ]
    )


def _cost_section(marts: Marts, assumptions: Assumptions, profiles: list[ContactProfile]) -> str:
    """Handling cost of a dispute under stated assumptions."""
    totals = _dispute_totals(marts)
    profile = next(
        (p for p in profiles if p.category == assumptions.handling_reason_category), None
    )
    if profile is None and profiles:
        raise ValueError(
            f"handling_reason_category {assumptions.handling_reason_category!r} matches no "
            "reason category in the data"
        )
    per_case = _handling_cost(assumptions, profile)
    base_costs = [
        cost
        for other in profiles
        if (cost := _handling_cost(assumptions, other)["base"]) is not None
    ]
    category_low = min(base_costs, default=None)
    category_high = max(base_costs, default=None)
    alternatives = [
        [
            other.category,
            _number(other.handle_minutes),
            _usd(_handling_cost(assumptions, other)["base"]),
        ]
        for other in profiles
    ]
    monthly_cases = _ratio(totals["cases"], totals["months"])
    monthly = {
        key: None if value is None or monthly_cases is None else value * monthly_cases
        for key, value in per_case.items()
    }
    return "\n".join(
        [
            "## 5. Agent handling cost per dispute",
            "",
            "The source records neither the cost of an agent nor the contacts a dispute needs, "
            "and contacts cannot be tied to cases, so the cost is a model, not a measurement:",
            "",
            "`cost per dispute = contacts per dispute * handling time * agent-hour cost`",
            "",
            f"- Handling time: mean duration of `{assumptions.handling_reason_category}` contacts, "
            f"**{_number(profile.handle_minutes if profile else None, 1)} minutes** (measured).",
            f"- Agent-hour cost: USD {assumptions.agent_hour_usd.low:g} / "
            f"{assumptions.agent_hour_usd.base:g} / {assumptions.agent_hour_usd.high:g} "
            "(low / base / high; assumption).",
            f"- Contacts per dispute: {assumptions.contacts_per_dispute.low:g} / "
            f"{assumptions.contacts_per_dispute.base:g} / "
            f"{assumptions.contacts_per_dispute.high:g} (low / base / high; assumption).",
            "",
            _table(
                ["", "Low", "Base", "High"],
                [
                    [
                        "Cost per dispute",
                        _usd(per_case["low"]),
                        _usd(per_case["base"]),
                        _usd(per_case["high"]),
                    ],
                    [
                        f"Cost per month at {_number(monthly_cases, 0)} cases",
                        _usd(monthly["low"]),
                        _usd(monthly["base"]),
                        _usd(monthly["high"]),
                    ],
                ],
            ),
            "",
            f"Taking another reason category as the model of a dispute contact moves the base cost "
            f"per dispute between {_usd(category_low)} and {_usd(category_high)}; the low-to-high "
            f"assumptions move it between {_usd(per_case['low'])} and {_usd(per_case['high'])}. "
            "At the base assumptions, the cost per dispute by category is",
            "",
            _table(
                ["Reason category", "Mean handling (min)", "Base cost per dispute"], alternatives
            ),
            "",
            "Waiting time is not costed: the customer waits, the agent does not. Back-office "
            "work on a case (investigation, contacting the merchant) is not recorded in the "
            "source and is not included, so agent time is understated by an unknown amount; the "
            "case for automation rests more on resolution time and SLA breaches (section 6) "
            "than on agent minutes. The assumptions live in "
            "`pipelines/analysis_assumptions.toml`.",
        ]
    )


def _kpi_section(marts: Marts, assumptions: Assumptions) -> str:
    """Outcomes an automated workflow should reach, against today's baseline."""
    totals = _dispute_totals(marts)
    median, _ = _resolution_days(marts)
    targets = assumptions.targets
    rows = [
        [
            "Safe automated resolution rate",
            "not measured today",
            f">= {_pct(targets.safe_automated_resolution_rate)}",
        ],
        [
            "SLA breach rate",
            _pct(_ratio(totals["sla_breached"], totals["cases"])),
            f"<= {_pct(targets.sla_breach_rate_max)}",
        ],
        [
            "Median days to resolution (resolved and closed cases only)",
            _number(median),
            f"<= {targets.median_days_to_resolution_max:g}",
        ],
        [
            "Repeat-complainer rate",
            _pct(_ratio(totals["repeat"], totals["cases"])),
            f"<= {_pct(targets.repeat_complainer_rate_max)}",
        ],
        [
            "Unsafe action rate",
            "not measured today",
            f"<= {_pct(targets.unsafe_action_rate_max)}",
        ],
    ]
    return "\n".join(
        [
            "## 6. Target outcomes for automation",
            "",
            "The customer outcome is a dispute that is understood, confirmed and filed correctly "
            "in one conversation, or handed to a person with everything they need; the business "
            "outcome is fewer agent hours per dispute without unsafe actions. The targets below "
            "are team-defined proposals that the evaluation measures the system against; the "
            "baseline is what the data shows today.",
            "",
            _table(["Outcome", "Baseline today", "Target"], rows),
            "",
            "The median is over the cases that reached `Resolved` or `Closed` "
            f"({_pct(_ratio(totals['closed'], totals['cases']))} of cases); the rest have no "
            "resolution time yet, so the baseline understates how long an open case waits.",
        ]
    )


def _lineage_section(manifest: Mapping[str, Any]) -> str:
    """Digests of the cleaned inputs and of the marts."""
    inputs = manifest["inputs"]
    marts = manifest["marts"]
    return "\n".join(
        [
            "## 7. Lineage",
            "",
            "Every figure above is computed from the marts below, which are built from the "
            "cleaned tables of the data-quality report.",
            "",
            _table(
                ["Cleaned table", "SHA-256"],
                [[name, inputs[name][:DIGEST_DIGITS]] for name in sorted(inputs)],
            ),
            "",
            _table(
                ["Mart", "Rows", "SHA-256"],
                [
                    [
                        f"`{name}`",
                        _count(marts[name]["rows"]),
                        marts[name]["sha256"][:DIGEST_DIGITS],
                    ]
                    for name in sorted(marts)
                ],
            ),
        ]
    )


def render_workflow_analysis(
    marts: Marts, assumptions: Assumptions, manifest: Mapping[str, Any]
) -> str:
    """Render the report from the marts, the assumptions and the manifest of the marts.

    Parameters
    ----------
    marts : Marts
        Rows of every mart, keyed by mart name.
    assumptions : Assumptions
        Cost assumptions and target outcomes.
    manifest : Mapping[str, Any]
        Manifest of the mart build (digests of inputs and marts).

    Returns
    -------
    str
        The Markdown report.
    """
    profiles = _contact_profiles(marts)
    sections = [
        "# Workflow analysis",
        "",
        "Which part of the service demand is about disputed transactions, how it is handled "
        "today and what an automated workflow should achieve. Every figure is computed by "
        "`make analyze` from the marts listed in section 7; assumptions are named as such.",
        "",
        _scope_section(marts),
        "",
        _demand_section(marts, profiles),
        "",
        _resolution_section(marts),
        "",
        _sentiment_section(marts, profiles),
        "",
        _cost_section(marts, assumptions, profiles),
        "",
        _kpi_section(marts, assumptions),
        "",
        _lineage_section(manifest),
    ]
    return "\n".join(sections) + "\n"


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Build the marts from the cleaned layer and write the report; return the exit code."""
    parser = argparse.ArgumentParser(description="Write the workflow analysis report.")
    parser.add_argument("--silver", type=Path, default=Path("data/silver"))
    parser.add_argument("--gold", type=Path, default=Path("data/gold/dispute_demand"))
    parser.add_argument("--report", type=Path, default=Path("reports/workflow-analysis.md"))
    parser.add_argument("--assumptions", type=Path, default=DEFAULT_ASSUMPTIONS)
    parser.add_argument("--code-version", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        assumptions = load_assumptions(args.assumptions)
        manifest = build_marts(
            args.silver, args.gold, code_version=args.code_version or git_version()
        )
        marts = {name: read_mart(args.gold, name) for name in MART_NAMES}
        text = render_workflow_analysis(marts, assumptions, manifest.as_dict())
    except (FileNotFoundError, ValueError, OSError) as error:
        # A report from an earlier run must never be mistaken for this run's result.
        logger.error("analysis_failed reason=%s", error)
        args.report.unlink(missing_ok=True)
        return 1
    except (Exception, KeyboardInterrupt):
        # Anything not already handled above (Ctrl-C is not an Exception, so it is named
        # explicitly), the same way: the stale report is gone rather than misleading.
        args.report.unlink(missing_ok=True)
        raise

    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(".md.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, args.report)
    logger.info("analysis_report_written report=%s", args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
