"""
Profile Report Renderer
=======================

Overview
--------
Turns a :class:`~pipelines.profile_models.DataProfile` into the Markdown report that documents
the state of the raw data: assumptions checked against the data, inventory, schema conformance,
key uniqueness, missing values, referential integrity, arrival lateness and workload facts.

Scope
-----
In: formatting and the verdict rules.
Out: measuring anything (``pipelines.profile``).

Design Principles
-----------------
- Pure function of the profile: no clock, no filesystem, no randomness, so the same data always
  yields byte-identical text.
- Every figure in the text comes from a count in the profile; rates are derived here.

Runtime Contract
----------------
``render_markdown(profile) -> str``
``assess_assumptions(profile) -> tuple[Assumption, ...]``

Limitations
-----------
Verdict thresholds are documented next to each rule and are judgement calls, not statistics.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass  # Result object for one assumption check
from enum import StrEnum  # Closed set of verdicts
from statistics import median  # Typical missing-value rate across columns

# Local modules
from pipelines.profile_models import (  # Measured facts consumed by the renderer
    ColumnProfile,
    DataProfile,
    DomainFacts,
    TableProfile,
    ValueCount,
)
from pipelines.sources import table as source_table  # Table role and primary key

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

# A table whose row count is within this share of the dictionary's figure is considered a match.
ROW_COUNT_TOLERANCE = 0.05
# The dictionary states roughly 2 % duplicates and 5 % nulls; bands of +-1 and +-2 points apply.
DUPLICATE_BAND = (0.01, 0.03)
NULL_BAND = (0.03, 0.07)
# Orphan references are "small" when they stay at or below this share of non-null references.
ORPHAN_LIMIT = 0.02
# amount_usd is usable when present in most rows and consistent with the daily rate in almost all.
USD_PRESENCE_MINIMUM = 0.95
USD_CONSISTENCY_MINIMUM = 0.95
# Values listed per column in the appendix are truncated to keep the report readable.
APPENDIX_VALUES = 8
# Values listed for reasons and categories in the workload section.
REASON_CATEGORIES_SHOWN = 12
DISTRIBUTION_SHOWN = 25
# Text shown where a figure cannot be computed.
NOT_AVAILABLE = "n/a"


class Verdict(StrEnum):
    """Outcome of checking one assumption against the measured data."""

    HOLDS = "Holds"
    DIFFERS = "Differs"
    INFORMATIONAL = "Informational"
    NOT_ASSESSED = "Not assessed"  # nothing was available to check the assumption against


@dataclass(frozen=True, slots=True)
class Assumption:
    """One claim about the data, what was observed and the resulting verdict."""

    statement: str
    expected: str
    observed: str
    verdict: Verdict


# -----------------------------------------------------------------------------
# Formatting helpers
# -----------------------------------------------------------------------------


def _count(value: int) -> str:
    """Format an integer with thousands separators."""
    return f"{value:,}"


def _percent(numerator: float, denominator: float, digits: int = 2) -> str:
    """Format a share as a percentage, or ``n/a`` when the denominator is zero."""
    if not denominator:
        return NOT_AVAILABLE
    return f"{100 * numerator / denominator:.{digits}f} %"


def _mebibytes(size: int) -> str:
    """Format a byte count in MiB."""
    return f"{size / 1024 / 1024:,.1f} MiB"


def _optional(value: float | int | None) -> str:
    """Format an optional number, using ``n/a`` for missing values."""
    if value is None:
        return NOT_AVAILABLE
    return f"{value:,}" if isinstance(value, int) else f"{value:,.2f}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a Markdown table; an empty row list renders a single 'none' row."""
    body = rows or [["none", *[""] * (len(headers) - 1)]]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in body)
    return "\n".join(lines)


def _inline(value: str) -> str:
    """Make a category value safe to show inside a Markdown code span on one line."""
    return " ".join(value.split()).replace("`", "'")


def _values(values: tuple[ValueCount, ...], limit: int) -> str:
    """Format the leading values of a distribution as ``value (count)``."""
    shown = [f"`{_inline(item.value)}` ({_count(item.count)})" for item in values[:limit]]
    if len(values) > limit:
        shown.append(f"… +{len(values) - limit} more")
    return ", ".join(shown)


# -----------------------------------------------------------------------------
# Assumption checks
# -----------------------------------------------------------------------------


def _nullable_cells(tables: tuple[TableProfile, ...]) -> tuple[int, int]:
    """Return (missing cells, total cells) over all columns the dictionary allows to be null."""
    missing = total = 0
    for profile in tables:
        for column in profile.columns:
            if column.declared_nullable:
                missing += column.nulls
                total += column.rows
    return missing, total


def _verdict(condition: bool, *, assessed: bool) -> Verdict:
    """HOLDS or DIFFERS from ``condition``, or NOT_ASSESSED when there was nothing to check."""
    if not assessed:
        return Verdict.NOT_ASSESSED
    return Verdict.HOLDS if condition else Verdict.DIFFERS


def _layout_assumptions(tables: tuple[TableProfile, ...]) -> list[Assumption]:
    """Assumptions about paths, encoding, headers and row counts."""
    nonconforming = sum(t.inventory.nonconforming_paths for t in tables)
    without_file = sum(t.inventory.missing_partition_days for t in tables)
    files = sum(t.inventory.files for t in tables)
    matching = sum(t.inventory.files_matching_declared_header for t in tables)
    undecodable = sum(t.inventory.undecodable_headers for t in tables)
    invalid = sum(t.inventory.invalid_headers for t in tables)
    with_bom = sum(t.inventory.bom_files for t in tables)
    deviating = [
        t.name
        for t in tables
        if t.expected_rows and abs(t.rows / t.expected_rows - 1) > ROW_COUNT_TOLERANCE
    ]
    return [
        Assumption(
            "Fact tables are partitioned as year/month/day, one file per day",
            "No path deviates from the layout",
            f"{_count(nonconforming)} non-conforming paths; "
            f"{_count(without_file)} calendar days without a file",
            _verdict(nonconforming == 0, assessed=files > 0),
        ),
        Assumption(
            "Files are UTF-8 CSV whose header equals the dictionary's column list",
            "Every file decodes and matches",
            f"{_count(matching)} of {_count(files)} headers match; "
            f"{_count(undecodable)} undecodable; {_count(invalid)} without a header row; "
            f"{_count(with_bom)} files start with a byte-order mark",
            _verdict(matching == files and undecodable == 0 and invalid == 0, assessed=files > 0),
        ),
        Assumption(
            "Row counts match the dictionary",
            f"Within {_percent(ROW_COUNT_TOLERANCE, 1, 0)} of the stated figure",
            "all tables within tolerance"
            if not deviating
            else "outside tolerance: " + ", ".join(deviating),
            _verdict(not deviating, assessed=bool(tables)),
        ),
    ]


def _lateness_text(lagged: list[TableProfile], stamped: list[TableProfile]) -> str:
    """Describe positive lags and rows stamped after their partition day."""
    parts = [
        f"{t.name}: {_count(t.lateness.lagged_over_7_days)} rows over 7 days, "
        f"max {_optional(t.lateness.lag_max)} days"
        for t in lagged
        if t.lateness
    ]
    parts += [
        f"{t.name}: {_count(t.lateness.stamped_after_partition)} rows stamped after their "
        f"partition day (event hours {t.lateness.stamped_after_first_hour}"
        f" to {t.lateness.stamped_after_last_hour})"
        for t in stamped
        if t.lateness
    ]
    return "; ".join(parts) or "no positive lag measured"


def _quality_assumptions(tables: tuple[TableProfile, ...]) -> list[Assumption]:
    """Assumptions about duplicates, missing values, references, drift and lateness."""
    extra = sum(t.key.extra_rows for t in tables)
    total_rows = sum(t.rows for t in tables)
    duplicate_share = extra / total_rows if total_rows else 0.0
    rates = [t.key.duplicate_rate for t in tables]
    per_table = f"per table {_percent(min(rates), 1)} to {_percent(max(rates), 1)}" if rates else ""
    missing_cells, cells = _nullable_cells(tables)
    null_share = missing_cells / cells if cells else 0.0
    column_rates = [c.null_rate for t in tables for c in t.columns if c.declared_nullable]
    typical = f"; median column {_percent(median(column_rates), 1)}" if column_rates else ""
    references = [(t.name, fk) for t in tables for fk in t.foreign_keys if fk.checked]
    worst_table, worst_fk = max(
        references, key=lambda item: item[1].orphan_rate, default=(None, None)
    )
    worst_rate = worst_fk.orphan_rate if worst_fk else 0.0
    worst_reference = (
        f"worst {worst_table}.{worst_fk.column} → {worst_fk.ref_table} "
        f"({_percent(worst_fk.orphans, worst_fk.checked)})"
        if worst_fk
        else "no reference could be checked"
    )
    checked = sum(fk.checked for _, fk in references)
    orphans = sum(fk.orphans for _, fk in references)
    skipped = sum(len(t.skipped_references) for t in tables)
    skipped_note = f"; {_count(skipped)} references could not be checked" if skipped else ""
    drifting = [t.name for t in tables if len(t.inventory.header_variants) > 1]
    measured = [t for t in tables if t.lateness and t.lateness.rows_measured]
    lagged = [t for t in measured if t.lateness and (t.lateness.lag_max or 0) > 0]
    stamped = [t for t in measured if t.lateness and t.lateness.stamped_after_partition]
    return [
        Assumption(
            "About 2 % of records are duplicates",
            f"{_percent(DUPLICATE_BAND[0], 1, 0)} to {_percent(DUPLICATE_BAND[1], 1, 0)} overall",
            f"{_percent(extra, total_rows)} overall; {per_table}",
            _verdict(
                DUPLICATE_BAND[0] <= duplicate_share <= DUPLICATE_BAND[1], assessed=total_rows > 0
            ),
        ),
        Assumption(
            "About 5 % of values are missing in nullable fields",
            f"{_percent(NULL_BAND[0], 1, 0)} to {_percent(NULL_BAND[1], 1, 0)} of nullable cells",
            f"{_percent(missing_cells, cells)} of {_count(cells)} nullable cells{typical}",
            _verdict(NULL_BAND[0] <= null_share <= NULL_BAND[1], assessed=cells > 0),
        ),
        Assumption(
            "Only a small share of foreign keys are orphans",
            f"Every reference at most {_percent(ORPHAN_LIMIT, 1, 0)} orphans "
            "(judged per reference: one broken reference invalidates joins on it whatever the "
            "overall share)",
            f"{_count(orphans)} of {_count(checked)} references overall "
            f"({_percent(orphans, checked)}); {worst_reference}{skipped_note}",
            _verdict(worst_rate <= ORPHAN_LIMIT, assessed=checked > 0),
        ),
        Assumption(
            "Schemas may evolve between partitions",
            "Drift is possible and must be handled",
            "tables with more than one header: "
            + (", ".join(drifting) if drifting else "none in this snapshot"),
            Verdict.INFORMATIONAL if tables else Verdict.NOT_ASSESSED,
        ),
        Assumption(
            "Partitions can arrive after the day they describe",
            "Some rows have a positive lag",
            _lateness_text(lagged, stamped),
            _verdict(bool(lagged), assessed=bool(measured)),
        ),
    ]


def _workload_assumptions(facts: DomainFacts) -> list[Assumption]:
    """Assumptions about the fraud label, USD amounts, transcripts and complaints."""
    fraud, usd, contacts, complaints = (
        facts.fraud,
        facts.usd_amounts,
        facts.contacts,
        facts.complaints,
    )
    comparable = usd.within_tolerance + usd.outside_tolerance
    consistent = usd.within_tolerance / comparable if comparable else 0.0
    presence = usd.present / fraud.transactions if fraud.transactions else 0.0
    usable = presence >= USD_PRESENCE_MINIMUM and consistent >= USD_CONSISTENCY_MINIMUM
    # Consistency can only be judged where a rate exists; presence alone can be judged always.
    checkable = fraud.transactions > 0 and (usd.present == 0 or comparable > 0)
    informational = Verdict.INFORMATIONAL
    return [
        Assumption(
            "The fraud label supports a supervised risk model",
            "Enough positive examples; prevalence known",
            f"{_count(fraud.positives)} positives in {_count(fraud.transactions)} transactions "
            f"({_percent(fraud.positives, fraud.transactions, 3)})",
            informational if fraud.transactions else Verdict.NOT_ASSESSED,
        ),
        Assumption(
            "amount_usd is present and consistent with the daily exchange rate",
            f"Present in at least {_percent(USD_PRESENCE_MINIMUM, 1, 0)} of rows and consistent "
            f"in at least {_percent(USD_CONSISTENCY_MINIMUM, 1, 0)}",
            f"present in {_percent(usd.present, fraud.transactions)} of rows; "
            f"{_percent(usd.within_tolerance, comparable)} of {_count(comparable)} comparable "
            "values within tolerance",
            _verdict(usable, assessed=checkable),
        ),
        Assumption(
            "Transcripts exist for interactions flagged as having one",
            "About one transcript per flagged interaction",
            f"{_count(contacts.flagged_with_transcript)} interactions flagged; "
            f"{_count(contacts.transcript_interactions)} distinct interactions have a transcript",
            informational if contacts.interactions else Verdict.NOT_ASSESSED,
        ),
        Assumption(
            "Complaint data contains dispute-like categories and a repeat-complainer signal",
            "Categories and a repeat flag are present",
            f"{_count(len(complaints.categories))} categories; repeat complainers "
            f"{_percent(complaints.repeat_complainers, complaints.complaints)}",
            informational if complaints.complaints else Verdict.NOT_ASSESSED,
        ),
    ]


def assess_assumptions(profile: DataProfile) -> tuple[Assumption, ...]:
    """Check each assumption about the data against the measurements.

    Parameters
    ----------
    profile : DataProfile
        Result of a profiling run.

    Returns
    -------
    tuple[Assumption, ...]
        One entry per assumption, in a fixed order.
    """
    results = _layout_assumptions(profile.tables) + _quality_assumptions(profile.tables)
    if profile.facts is not None:
        results += _workload_assumptions(profile.facts)
    return tuple(results)


# -----------------------------------------------------------------------------
# Sections
# -----------------------------------------------------------------------------


def _inventory_section(tables: tuple[TableProfile, ...]) -> str:
    """Files, size, row counts against the dictionary and partition span per table."""
    rows = []
    for profile in tables:
        inventory = profile.inventory
        partitioned = inventory.first_partition is not None
        span = (
            f"{inventory.first_partition} → {inventory.last_partition}"
            if partitioned
            else "single file"
        )
        rows.append(
            [
                profile.name,
                source_table(profile.name).kind.value,
                _count(inventory.files),
                _mebibytes(inventory.total_bytes),
                _count(profile.rows),
                _count(profile.expected_rows),
                _percent(profile.rows, profile.expected_rows, 1),
                span,
                _count(inventory.missing_partition_days) if partitioned else NOT_AVAILABLE,
            ]
        )
    headers = ["Table", "Kind", "Files", "Size", "Rows", "Dictionary rows", "Rows / dictionary"]
    return _table([*headers, "Partition span", "Days without file"], rows)


def _schema_section(tables: tuple[TableProfile, ...]) -> str:
    """Header conformance, byte-order marks, path conformance and column differences."""
    rows = [
        [
            t.name,
            f"{_count(t.inventory.files_matching_declared_header)} / {_count(t.inventory.files)}",
            _count(len(t.inventory.header_variants)),
            _count(t.inventory.bom_files),
            _count(t.inventory.undecodable_headers),
            _count(t.inventory.invalid_headers),
            _count(t.inventory.nonconforming_paths),
            ", ".join(t.extra_columns) or "none",
            ", ".join(t.missing_columns) or "none",
        ]
        for t in tables
    ]
    headers = ["Table", "Headers matching dictionary", "Header variants"]
    headers += ["Files with byte-order mark", "Undecodable headers", "Files without a header row"]
    headers += ["Non-conforming paths"]
    return _table([*headers, "Extra columns", "Missing columns"], rows)


def _key_section(tables: tuple[TableProfile, ...]) -> str:
    """Primary-key uniqueness and the nature of the repeated keys."""
    rows = [
        [
            t.name,
            ", ".join(source_table(t.name).primary_key),
            _count(t.key.rows),
            _count(t.key.distinct_keys),
            _count(t.key.null_keys),
            _count(t.key.extra_rows),
            _percent(t.key.extra_rows, t.key.rows),
            _count(t.key.identical_groups),
            _count(t.key.conflicting_groups),
        ]
        for t in tables
    ]
    headers = ["Table", "Primary key", "Rows", "Distinct keys", "Rows with a missing key"]
    headers += ["Extra rows", "Duplicate rate", "Identical re-deliveries", "Conflicting versions"]
    return _table(headers, rows)


def _quality_issues(column: ColumnProfile) -> list[tuple[str, int]]:
    """Describe the data-quality problems found in one column as (description, affected rows)."""
    issues: list[tuple[str, int]] = []
    if column.violates_declared_not_null:
        issues.append(("missing values in a NOT NULL column", column.nulls))
    if column.unparseable:
        issues.append((f"values that do not parse as {column.dtype}", column.unparseable))
    if column.integers_written_as_decimals:
        issues.append(
            ("integers written with a decimal point", column.integers_written_as_decimals)
        )
    if column.text_encoding_suspects:
        issues.append(("values with damaged text encoding", column.text_encoding_suspects))
    return issues


def _missing_values_section(tables: tuple[TableProfile, ...]) -> str:
    """Missing-value shares per table and the columns with contract-relevant problems."""
    summary: list[list[str]] = []
    problems: list[list[str]] = []
    for profile in tables:
        nullable = [c for c in profile.columns if c.declared_nullable]
        missing = sum(c.nulls for c in nullable)
        cells = sum(c.rows for c in nullable)
        summary.append([profile.name, _count(len(nullable)), _percent(missing, cells)])
        for column in profile.columns:
            for description, affected in _quality_issues(column):
                where = f"{profile.name}.{column.name}"
                problems.append(
                    [where, description, _count(affected), _percent(affected, column.rows)]
                )
    return (
        _table(["Table", "Nullable columns", "Missing share of nullable cells"], summary)
        + "\n\nColumns with contract-relevant problems:\n\n"
        + _table(["Column", "Problem", "Rows", "Share of rows"], problems)
    )


def _reference_section(tables: tuple[TableProfile, ...]) -> str:
    """Orphan references per declared foreign key, and the keys that could not be checked."""
    rows = [
        [
            t.name,
            f"{fk.column} → {fk.ref_table}.{fk.ref_column}",
            _count(fk.checked),
            _count(fk.orphans),
            _percent(fk.orphans, fk.checked),
        ]
        for t in tables
        for fk in t.foreign_keys
    ]
    skipped = [
        [t.name, f"{ref.column} → {ref.ref_table}.{ref.ref_column}", ref.reason]
        for t in tables
        for ref in t.skipped_references
    ]
    text = _table(["Table", "Reference", "Non-null references", "Orphans", "Orphan rate"], rows)
    if skipped:
        text += "\n\nReferences that could not be checked:\n\n" + _table(
            ["Table", "Reference", "Reason"], skipped
        )
    return text


def _lateness_section(tables: tuple[TableProfile, ...]) -> str:
    """Arrival lag per fact table."""
    rows = []
    for profile in tables:
        lateness = profile.lateness
        if lateness is None:
            continue
        hours = (
            f"{lateness.stamped_after_first_hour} to {lateness.stamped_after_last_hour}"
            if lateness.stamped_after_first_hour is not None
            else NOT_AVAILABLE
        )
        rows.append(
            [
                profile.name,
                _count(lateness.rows_measured),
                _count(lateness.partition_process_date_mismatches),
                _count(lateness.stamped_after_partition),
                hours,
                _optional(lateness.lag_min),
                _optional(lateness.lag_p50),
                _optional(lateness.lag_p95),
                _optional(lateness.lag_p99),
                _optional(lateness.lag_max),
                _count(lateness.lagged_over_7_days),
                _count(lateness.lagged_over_30_days),
            ]
        )
    headers = ["Table", "Rows measured", "Partition ≠ process_date", "Event after partition day"]
    headers += ["Hours of those events", "Lag min (days)", "p50", "p95", "p99", "Max"]
    headers += ["Over 7 days", "Over 30 days"]
    return (
        "Lag is the partition day minus the event day; positive values arrived after the day "
        "they describe. Events stamped after their own partition day that cluster in the first "
        "hours of the day point to partitions cut in a different time zone from the "
        "timestamps.\n\n" + _table(headers, rows)
    )


def _workload_section(profile: DataProfile) -> str:
    """Fraud label, USD amounts, contact centre and complaints."""
    facts = profile.facts
    if facts is None:
        return "No workload facts were measured."
    fraud, usd, contacts, complaints = (
        facts.fraud,
        facts.usd_amounts,
        facts.contacts,
        facts.complaints,
    )
    monthly = _table(
        ["Month", "Transactions", "Fraud positives", "Prevalence"],
        [
            [m.month, _count(m.rows), _count(m.positives), _percent(m.positives, m.rows, 3)]
            for m in fraud.by_month
            if m.rows
        ],
    )
    fraud_text = (
        f"{_count(fraud.positives)} of {_count(fraud.transactions)} transactions are labelled "
        f"as fraud ({_percent(fraud.positives, fraud.transactions, 3)})."
    )
    usd_text = (
        f"`amount_usd` is present in {_count(usd.present)} rows. Of those, "
        f"{_count(usd.within_tolerance)} equal the local amount converted at the daily rate "
        f"within tolerance, {_count(usd.outside_tolerance)} do not, and "
        f"{_count(usd.without_rate)} cannot be checked because no rate exists for their "
        "currency and day."
    )
    contact_text = (
        f"{_count(contacts.interactions)} interactions, of which "
        f"{_count(contacts.flagged_with_transcript)} are flagged as having a transcript; the "
        f"transcripts table holds {_count(contacts.transcripts)} transcripts covering "
        f"{_count(contacts.transcript_interactions)} distinct interactions."
    )
    complaint_text = (
        f"{_count(complaints.complaints)} complaints; "
        f"{_percent(complaints.repeat_complainers, complaints.complaints)} come from repeat "
        f"complainers and {_percent(complaints.sla_breached, complaints.complaints)} breached "
        "their SLA."
    )
    reason_categories = _values(contacts.reason_categories, REASON_CATEGORIES_SHOWN) or "none"
    reasons = _values(contacts.contact_reasons, DISTRIBUTION_SHOWN) or "none"
    categories = _values(complaints.categories, DISTRIBUTION_SHOWN) or "none"
    return "\n\n".join(
        [
            f"### Fraud label\n\n{fraud_text}\n\n{monthly}",
            f"### USD amounts\n\n{usd_text}",
            f"### Contact centre\n\n{contact_text}\n\nReason categories: {reason_categories}"
            f"\n\nContact reasons: {reasons}",
            f"### Complaints\n\n{complaint_text}\n\nCategories: {categories}",
        ]
    )


def _not_profiled_section(profile: DataProfile) -> str:
    """Tables that were expected but not measured, with the reason."""
    rows = [[name, "no files found"] for name in profile.absent_tables]
    rows += [[t.name, t.reason] for t in profile.unloadable_tables]
    return _table(["Table", "Reason"], rows)


def _appendix(tables: tuple[TableProfile, ...]) -> str:
    """Column-level detail for every table."""
    blocks = []
    headers = ["Column", "Type", "Nullable", "Missing", "Unparseable", "Distinct (estimate)"]
    for profile in tables:
        rows = [
            [
                c.name,
                c.dtype,
                "yes" if c.declared_nullable else "no",
                _percent(c.nulls, c.rows),
                _count(c.unparseable),
                _count(c.distinct_estimate),
                _values(c.top_values, APPENDIX_VALUES) if c.top_values else "",
            ]
            for c in profile.columns
        ]
        blocks.append(f"### {profile.name}\n\n" + _table([*headers, "Values"], rows))
    return "\n\n".join(blocks)


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def render_markdown(profile: DataProfile) -> str:
    """Render the profile as a Markdown report.

    Parameters
    ----------
    profile : DataProfile
        Result of a profiling run.

    Returns
    -------
    str
        The complete report, ending with a newline.
    """
    tables = profile.tables
    verdicts = _table(
        ["Assumption", "Expected", "Observed", "Verdict"],
        [
            [a.statement, a.expected, a.observed, a.verdict.value]
            for a in assess_assumptions(profile)
        ],
    )
    introduction = (
        f"Snapshot digest (SHA-256 over file paths and sizes): `{profile.inventory_digest}`\n\n"
        f"Tables profiled: {_count(len(tables))}. Every figure below is computed from the raw "
        "files by `make profile`; nothing is typed in by hand. Verdicts cover only the tables "
        "that were profiled; tables that were absent or could not be parsed are listed in "
        "section 2."
    )
    key_note = (
        "Repeated keys are *identical* when the rows differ only by `process_date` (the same "
        "record delivered again) and *conflicting* when other columns differ."
    )
    sections = [
        "# Raw Data Profile",
        introduction,
        "## 1. Assumptions checked against the data\n\n" + verdicts,
        "## 2. Tables not profiled\n\n" + _not_profiled_section(profile),
        "## 3. Inventory\n\n" + _inventory_section(tables),
        "## 4. Files and schema\n\n" + _schema_section(tables),
        f"## 5. Keys and duplicates\n\n{key_note}\n\n" + _key_section(tables),
        "## 6. Missing and malformed values\n\n" + _missing_values_section(tables),
        "## 7. Referential integrity\n\n" + _reference_section(tables),
        "## 8. Arrival lateness\n\n" + _lateness_section(tables),
        "## 9. Workload facts\n\n" + _workload_section(profile),
        "## Appendix. Column detail\n\n" + _appendix(tables),
    ]
    return "\n\n".join(sections) + "\n"
