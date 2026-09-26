"""
Profile Result Models
=====================

Overview
--------
Immutable result objects produced by the raw-data profiler and consumed by the report
renderer. Keeping them apart from the query code lets the renderer and its tests work on
plain values, without a database.

Scope
-----
In: value objects describing measured facts.
Out: how the facts are measured (``pipelines.profile``) or presented (``pipelines.profile_report``).

Design Principles
-----------------
- Frozen dataclasses with tuples instead of lists, so a profile is hashable and cannot change
  after it has been computed.
- Counts are stored raw; rates are derived by properties so they can never disagree with the
  counts they come from.

Runtime Contract
----------------
``DataProfile`` is the root object; ``dataclasses.asdict`` yields a JSON-serializable form.

Limitations
-----------
Percentiles are computed by the database engine and rounded by the renderer, not here.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass  # Immutable value objects

# Local modules
from pipelines.inventory import TableInventory  # Filesystem facts embedded in each table profile

# -----------------------------------------------------------------------------
# Column and key level facts
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValueCount:
    """A distinct value and how many rows carry it (``<null>`` stands for a missing value)."""

    value: str
    count: int


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    """Measured facts about one declared column."""

    name: str
    dtype: str
    declared_nullable: bool
    rows: int
    nulls: int
    unparseable: int
    integers_written_as_decimals: int
    text_encoding_suspects: int
    distinct_estimate: int
    top_values: tuple[ValueCount, ...]

    @property
    def null_rate(self) -> float:
        """Share of rows where the value is missing."""
        return self.nulls / self.rows if self.rows else 0.0

    @property
    def violates_declared_not_null(self) -> bool:
        """True when the dictionary says NOT NULL but missing values exist."""
        return not self.declared_nullable and self.nulls > 0


@dataclass(frozen=True, slots=True)
class KeyProfile:
    """Primary-key uniqueness facts."""

    rows: int
    distinct_keys: int
    duplicate_groups: int
    identical_groups: int
    conflicting_groups: int

    @property
    def extra_rows(self) -> int:
        """Rows that repeat an already-seen key."""
        return self.rows - self.distinct_keys

    @property
    def duplicate_rate(self) -> float:
        """Share of rows that are repeats of another row with the same key."""
        return self.extra_rows / self.rows if self.rows else 0.0


@dataclass(frozen=True, slots=True)
class ForeignKeyProfile:
    """Referential-integrity facts for one foreign key."""

    column: str
    ref_table: str
    ref_column: str
    checked: int
    orphans: int

    @property
    def orphan_rate(self) -> float:
        """Share of non-null references that point at no existing row."""
        return self.orphans / self.checked if self.checked else 0.0


@dataclass(frozen=True, slots=True)
class LatenessProfile:
    """How long after the event a row arrived, measured against its partition day.

    ``lag_days`` is partition day minus event day: positive means the row arrived after the
    day it describes; negative means the event is stamped after its partition.
    """

    rows_measured: int
    partition_process_date_mismatches: int
    lag_min: int | None
    lag_p50: float | None
    lag_p95: float | None
    lag_p99: float | None
    lag_max: int | None
    stamped_after_partition: int
    lagged_over_7_days: int
    lagged_over_30_days: int


@dataclass(frozen=True, slots=True)
class TableProfile:
    """Everything measured about one table."""

    name: str
    inventory: TableInventory
    expected_rows: int
    key: KeyProfile
    extra_columns: tuple[str, ...]
    missing_columns: tuple[str, ...]
    columns: tuple[ColumnProfile, ...]
    foreign_keys: tuple[ForeignKeyProfile, ...]
    lateness: LatenessProfile | None

    @property
    def rows(self) -> int:
        """Total rows loaded, duplicates included."""
        return self.key.rows


# -----------------------------------------------------------------------------
# Workload facts
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MonthlyCount:
    """Rows and positive labels in one calendar month."""

    month: str
    rows: int
    positives: int


@dataclass(frozen=True, slots=True)
class FraudFacts:
    """Prevalence of the fraud label, overall and by calendar month."""

    transactions: int
    positives: int
    by_month: tuple[MonthlyCount, ...]

    @property
    def prevalence(self) -> float:
        """Share of transactions labelled as fraud."""
        return self.positives / self.transactions if self.transactions else 0.0


@dataclass(frozen=True, slots=True)
class UsdAmountFacts:
    """How consistent the stated USD amount is with the amount and the daily exchange rate."""

    present: int
    within_tolerance: int
    outside_tolerance: int
    without_rate: int


@dataclass(frozen=True, slots=True)
class ContactFacts:
    """Contact-centre volume, reasons and transcript availability."""

    interactions: int
    flagged_with_transcript: int
    transcripts: int
    transcript_interactions: int
    contact_reasons: tuple[ValueCount, ...]
    reason_categories: tuple[ValueCount, ...]


@dataclass(frozen=True, slots=True)
class ComplaintFacts:
    """Complaint volume, repeat complainers, SLA breaches and categories."""

    complaints: int
    repeat_complainers: int
    sla_breached: int
    categories: tuple[ValueCount, ...]


@dataclass(frozen=True, slots=True)
class DomainFacts:
    """Facts that decide which workflow to build and how to evaluate it."""

    fraud: FraudFacts
    usd_amounts: UsdAmountFacts
    contacts: ContactFacts
    complaints: ComplaintFacts


@dataclass(frozen=True, slots=True)
class DataProfile:
    """Root object of a profiling run."""

    inventory_digest: str
    tables: tuple[TableProfile, ...]
    facts: DomainFacts | None
