"""
Source Table Registry
=====================

Overview
--------
Declarative description of the 13 source tables: role, on-disk layout, expected row count,
primary key, foreign keys and the column list with SQL types and nullability, exactly as the
source data dictionary states them. Profiling, contracts and pipelines all read this one
registry, so a table is described in a single place.

Scope
-----
In: static metadata about the source tables.
Out: reading data, validation logic and any I/O.

Design Principles
-----------------
- Frozen value objects; the registry is a module-level constant, never mutated.
- Column names, types and nullability are the dictionary's claims. They are verified against the
  data by profiling, not assumed.

Runtime Contract
----------------
``TABLES``: ordered tuple of :class:`TableSpec`; ``table(name)`` returns one by name.

Limitations
-----------
Nullability comes from the dictionary's ``NOT NULL`` markers; a column the dictionary leaves
unmarked is treated as nullable.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass  # Immutable value objects for the registry
from enum import StrEnum  # Closed sets for table kind and on-disk layout

# -----------------------------------------------------------------------------
# Types
# -----------------------------------------------------------------------------


class TableKind(StrEnum):
    """Role of a table in the source model."""

    DIMENSION = "dimension"
    FACT = "fact"
    REFERENCE = "reference"


class Layout(StrEnum):
    """How a table is laid out under the raw data directory."""

    SINGLE_FILE = "single_file"  # <name>.csv
    DAILY_PARTITIONS = "daily_partitions"  # <name>/year=YYYY/month=MM/day=DD/<name>_YYYYMMDD.csv


@dataclass(frozen=True, slots=True)
class Column:
    """One column as declared by the data dictionary."""

    name: str
    dtype: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class ForeignKey:
    """A reference from ``column`` to ``ref_table.ref_column``."""

    column: str
    ref_table: str
    ref_column: str


@dataclass(frozen=True, slots=True)
class TableSpec:
    """Static description of one source table."""

    name: str
    kind: TableKind
    layout: Layout
    expected_rows: int
    primary_key: tuple[str, ...]
    event_time_column: str | None
    columns: tuple[Column, ...]
    foreign_keys: tuple[ForeignKey, ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        """Declared column names in dictionary order."""
        return tuple(column.name for column in self.columns)


# -----------------------------------------------------------------------------
# Registry
# -----------------------------------------------------------------------------

TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        name="customers",
        kind=TableKind.DIMENSION,
        layout=Layout.SINGLE_FILE,
        expected_rows=150_000,
        primary_key=("customer_id",),
        event_time_column=None,
        columns=(
            Column("customer_id", "VARCHAR(20)", nullable=False),
            Column("document_number", "VARCHAR(20)", nullable=False),
            Column("document_type", "VARCHAR(10)", nullable=False),
            Column("first_name", "VARCHAR(100)", nullable=False),
            Column("last_name", "VARCHAR(100)", nullable=False),
            Column("date_of_birth", "DATE", nullable=False),
            Column("gender", "VARCHAR(1)", nullable=True),
            Column("email", "VARCHAR(100)", nullable=True),
            Column("mobile_phone", "VARCHAR(20)", nullable=True),
            Column("landline_phone", "VARCHAR(20)", nullable=True),
            Column("address", "VARCHAR(200)", nullable=True),
            Column("city", "VARCHAR(100)", nullable=False),
            Column("state", "VARCHAR(100)", nullable=False),
            Column("country", "VARCHAR(50)", nullable=False),
            Column("postal_code", "VARCHAR(10)", nullable=True),
            Column("detected_accent", "VARCHAR(50)", nullable=True),
            Column("segment", "VARCHAR(50)", nullable=False),
            Column("credit_score", "INTEGER", nullable=True),
            Column("estimated_monthly_income", "DECIMAL(12,2)", nullable=True),
            Column("occupation", "VARCHAR(100)", nullable=True),
            Column("marital_status", "VARCHAR(20)", nullable=True),
            Column("education_level", "VARCHAR(50)", nullable=True),
            Column("registration_date", "TIMESTAMP", nullable=False),
            Column("registration_branch_id", "VARCHAR(20)", nullable=False),
            Column("customer_status", "VARCHAR(20)", nullable=False),
            Column("last_updated", "TIMESTAMP", nullable=False),
            Column("accepts_marketing", "BOOLEAN", nullable=False),
        ),
        foreign_keys=(ForeignKey("registration_branch_id", "branches", "branch_id"),),
    ),
    TableSpec(
        name="products",
        kind=TableKind.DIMENSION,
        layout=Layout.SINGLE_FILE,
        expected_rows=400_000,
        primary_key=("product_id",),
        event_time_column=None,
        columns=(
            Column("product_id", "VARCHAR(20)", nullable=False),
            Column("customer_id", "VARCHAR(20)", nullable=False),
            Column("product_type", "VARCHAR(50)", nullable=True),
            Column("product_number", "VARCHAR(30)", nullable=False),
            Column("currency", "VARCHAR(3)", nullable=False),
            Column("current_balance", "DECIMAL(15,2)", nullable=False),
            Column("credit_limit", "DECIMAL(15,2)", nullable=True),
            Column("interest_rate", "DECIMAL(5,2)", nullable=True),
            Column("opening_date", "DATE", nullable=False),
            Column("expiration_date", "DATE", nullable=True),
            Column("opening_branch_id", "VARCHAR(20)", nullable=False),
            Column("product_status", "VARCHAR(20)", nullable=False),
            Column("opening_channel", "VARCHAR(30)", nullable=False),
            Column("has_linked_app", "BOOLEAN", nullable=False),
            Column("days_past_due", "INTEGER", nullable=True),
            Column("last_transaction_date", "TIMESTAMP", nullable=True),
            Column("last_updated", "TIMESTAMP", nullable=False),
        ),
        foreign_keys=(
            ForeignKey("customer_id", "customers", "customer_id"),
            ForeignKey("opening_branch_id", "branches", "branch_id"),
        ),
    ),
    TableSpec(
        name="branches",
        kind=TableKind.DIMENSION,
        layout=Layout.SINGLE_FILE,
        expected_rows=350,
        primary_key=("branch_id",),
        event_time_column=None,
        columns=(
            Column("branch_id", "VARCHAR(20)", nullable=False),
            Column("branch_code", "VARCHAR(10)", nullable=False),
            Column("branch_name", "VARCHAR(100)", nullable=False),
            Column("branch_type", "VARCHAR(30)", nullable=False),
            Column("address", "VARCHAR(200)", nullable=False),
            Column("city", "VARCHAR(100)", nullable=False),
            Column("state", "VARCHAR(100)", nullable=False),
            Column("country", "VARCHAR(50)", nullable=False),
            Column("postal_code", "VARCHAR(10)", nullable=True),
            Column("geographic_zone", "VARCHAR(50)", nullable=False),
            Column("phone", "VARCHAR(20)", nullable=False),
            Column("email", "VARCHAR(100)", nullable=True),
            Column("opening_time", "TIME", nullable=False),
            Column("closing_time", "TIME", nullable=False),
            Column("has_atms", "BOOLEAN", nullable=False),
            Column("atm_count", "INTEGER", nullable=True),
            Column("has_teller_windows", "BOOLEAN", nullable=False),
            Column("teller_window_count", "INTEGER", nullable=True),
            Column("latitude", "DECIMAL(10,7)", nullable=True),
            Column("longitude", "DECIMAL(10,7)", nullable=True),
            Column("branch_opening_date", "DATE", nullable=False),
            Column("branch_status", "VARCHAR(20)", nullable=False),
        ),
        foreign_keys=(),
    ),
    TableSpec(
        name="service_agents",
        kind=TableKind.DIMENSION,
        layout=Layout.SINGLE_FILE,
        expected_rows=1_200,
        primary_key=("agent_id",),
        event_time_column=None,
        columns=(
            Column("agent_id", "VARCHAR(20)", nullable=False),
            Column("employee_code", "VARCHAR(15)", nullable=False),
            Column("first_name", "VARCHAR(100)", nullable=False),
            Column("last_name", "VARCHAR(100)", nullable=False),
            Column("email", "VARCHAR(100)", nullable=False),
            Column("phone", "VARCHAR(20)", nullable=True),
            Column("native_accent", "VARCHAR(50)", nullable=False),
            Column("country_of_origin", "VARCHAR(50)", nullable=False),
            Column("assigned_branch_id", "VARCHAR(20)", nullable=True),
            Column("agent_type", "VARCHAR(30)", nullable=False),
            Column("experience_level", "VARCHAR(20)", nullable=False),
            Column("languages", "VARCHAR(100)", nullable=False),
            Column("specialty", "VARCHAR(100)", nullable=True),
            Column("hire_date", "DATE", nullable=False),
            Column("avg_csat", "DECIMAL(3,2)", nullable=True),
            Column("total_monthly_interactions", "INTEGER", nullable=True),
            Column("agent_status", "VARCHAR(20)", nullable=False),
            Column("work_shift", "VARCHAR(20)", nullable=False),
        ),
        foreign_keys=(ForeignKey("assigned_branch_id", "branches", "branch_id"),),
    ),
    TableSpec(
        name="marketing_campaigns",
        kind=TableKind.DIMENSION,
        layout=Layout.SINGLE_FILE,
        expected_rows=200,
        primary_key=("campaign_id",),
        event_time_column=None,
        columns=(
            Column("campaign_id", "VARCHAR(20)", nullable=False),
            Column("campaign_name", "VARCHAR(150)", nullable=False),
            Column("description", "TEXT", nullable=True),
            Column("campaign_type", "VARCHAR(50)", nullable=False),
            Column("campaign_objective", "VARCHAR(100)", nullable=True),
            Column("promoted_product", "VARCHAR(50)", nullable=True),
            Column("target_segment", "VARCHAR(50)", nullable=True),
            Column("target_country", "VARCHAR(50)", nullable=True),
            Column("start_date", "DATE", nullable=False),
            Column("end_date", "DATE", nullable=False),
            Column("budget", "DECIMAL(12,2)", nullable=True),
            Column("campaign_status", "VARCHAR(20)", nullable=False),
            Column("expected_conversion_rate", "DECIMAL(5,2)", nullable=True),
        ),
        foreign_keys=(),
    ),
    TableSpec(
        name="transactions",
        kind=TableKind.FACT,
        layout=Layout.DAILY_PARTITIONS,
        expected_rows=5_000_000,
        primary_key=("transaction_id",),
        event_time_column="transaction_date",
        columns=(
            Column("transaction_id", "VARCHAR(30)", nullable=False),
            Column("transaction_date", "TIMESTAMP", nullable=False),
            Column("process_date", "DATE", nullable=False),
            Column("product_id", "VARCHAR(20)", nullable=False),
            Column("customer_id", "VARCHAR(20)", nullable=False),
            Column("transaction_type", "VARCHAR(50)", nullable=True),
            Column("transaction_category", "VARCHAR(50)", nullable=True),
            Column("amount", "DECIMAL(15,2)", nullable=False),
            Column("currency", "VARCHAR(3)", nullable=False),
            Column("amount_usd", "DECIMAL(15,2)", nullable=True),
            Column("channel", "VARCHAR(30)", nullable=False),
            Column("branch_id", "VARCHAR(20)", nullable=True),
            Column("merchant_name", "VARCHAR(150)", nullable=True),
            Column("merchant_category", "VARCHAR(50)", nullable=True),
            Column("transaction_country", "VARCHAR(50)", nullable=False),
            Column("transaction_city", "VARCHAR(100)", nullable=True),
            Column("transaction_status", "VARCHAR(20)", nullable=False),
            Column("response_code", "VARCHAR(10)", nullable=True),
            Column("is_fraud", "BOOLEAN", nullable=False),
            Column("fraud_score", "DECIMAL(5,2)", nullable=True),
            Column("latitude", "DECIMAL(10,7)", nullable=True),
            Column("longitude", "DECIMAL(10,7)", nullable=True),
        ),
        foreign_keys=(
            ForeignKey("customer_id", "customers", "customer_id"),
            ForeignKey("branch_id", "branches", "branch_id"),
            ForeignKey("product_id", "products", "product_id"),
        ),
    ),
    TableSpec(
        name="call_center_interactions",
        kind=TableKind.FACT,
        layout=Layout.DAILY_PARTITIONS,
        expected_rows=800_000,
        primary_key=("interaction_id",),
        event_time_column="interaction_date",
        columns=(
            Column("interaction_id", "VARCHAR(30)", nullable=False),
            Column("interaction_date", "TIMESTAMP", nullable=False),
            Column("process_date", "DATE", nullable=False),
            Column("customer_id", "VARCHAR(20)", nullable=False),
            Column("agent_id", "VARCHAR(20)", nullable=True),
            Column("interaction_type", "VARCHAR(30)", nullable=False),
            Column("channel", "VARCHAR(30)", nullable=False),
            Column("contact_reason", "VARCHAR(100)", nullable=False),
            Column("reason_category", "VARCHAR(50)", nullable=True),
            Column("duration_seconds", "INTEGER", nullable=True),
            Column("wait_time_seconds", "INTEGER", nullable=True),
            Column("was_resolved", "BOOLEAN", nullable=True),
            Column("requires_followup", "BOOLEAN", nullable=False),
            Column("detected_sentiment", "VARCHAR(20)", nullable=True),
            Column("sentiment_score", "DECIMAL(3,2)", nullable=True),
            Column("customer_detected_accent", "VARCHAR(50)", nullable=True),
            Column("agent_used_accent", "VARCHAR(50)", nullable=True),
            Column("was_escalated", "BOOLEAN", nullable=False),
            Column("mentioned_products", "VARCHAR(200)", nullable=True),
            Column("has_transcript", "BOOLEAN", nullable=False),
            Column("has_recording", "BOOLEAN", nullable=False),
        ),
        foreign_keys=(
            ForeignKey("customer_id", "customers", "customer_id"),
            ForeignKey("agent_id", "service_agents", "agent_id"),
        ),
    ),
    TableSpec(
        name="call_transcripts",
        kind=TableKind.FACT,
        layout=Layout.DAILY_PARTITIONS,
        expected_rows=200_000,
        primary_key=("transcript_id",),
        event_time_column=None,
        columns=(
            Column("transcript_id", "VARCHAR(30)", nullable=False),
            Column("interaction_id", "VARCHAR(30)", nullable=False),
            Column("process_date", "DATE", nullable=False),
            Column("customer_id", "VARCHAR(20)", nullable=False),
            Column("agent_id", "VARCHAR(20)", nullable=False),
            Column("full_text", "TEXT", nullable=False),
            Column("customer_text", "TEXT", nullable=True),
            Column("agent_text", "TEXT", nullable=True),
            Column("detected_language", "VARCHAR(10)", nullable=False),
            Column("detected_accent", "VARCHAR(50)", nullable=True),
            Column("accent_confidence", "DECIMAL(3,2)", nullable=True),
            Column("detected_keywords", "VARCHAR(500)", nullable=True),
            Column("mentioned_entities", "TEXT", nullable=True),
            Column("detected_intents", "VARCHAR(300)", nullable=True),
            Column("main_topics", "VARCHAR(300)", nullable=True),
            Column("transcription_model", "VARCHAR(50)", nullable=False),
            Column("audio_quality", "VARCHAR(20)", nullable=True),
            Column("duration_seconds", "INTEGER", nullable=False),
        ),
        foreign_keys=(
            ForeignKey("customer_id", "customers", "customer_id"),
            ForeignKey("agent_id", "service_agents", "agent_id"),
            ForeignKey("interaction_id", "call_center_interactions", "interaction_id"),
        ),
    ),
    TableSpec(
        name="satisfaction_surveys",
        kind=TableKind.FACT,
        layout=Layout.DAILY_PARTITIONS,
        expected_rows=250_000,
        primary_key=("survey_id",),
        event_time_column="survey_date",
        columns=(
            Column("survey_id", "VARCHAR(30)", nullable=False),
            Column("survey_date", "TIMESTAMP", nullable=False),
            Column("process_date", "DATE", nullable=False),
            Column("interaction_id", "VARCHAR(30)", nullable=True),
            Column("customer_id", "VARCHAR(20)", nullable=False),
            Column("agent_id", "VARCHAR(20)", nullable=True),
            Column("survey_type", "VARCHAR(20)", nullable=False),
            Column("send_channel", "VARCHAR(30)", nullable=False),
            Column("main_score", "INTEGER", nullable=False),
            Column("nps_category", "VARCHAR(20)", nullable=True),
            Column("question_1_text", "TEXT", nullable=True),
            Column("question_1_response", "INTEGER", nullable=True),
            Column("question_2_text", "TEXT", nullable=True),
            Column("question_2_response", "INTEGER", nullable=True),
            Column("question_3_text", "TEXT", nullable=True),
            Column("question_3_response", "INTEGER", nullable=True),
            Column("open_comments", "TEXT", nullable=True),
            Column("comment_sentiment", "VARCHAR(20)", nullable=True),
            Column("response_time_hours", "DECIMAL(8,2)", nullable=True),
            Column("campaign_response_rate", "DECIMAL(5,2)", nullable=True),
        ),
        foreign_keys=(
            ForeignKey("customer_id", "customers", "customer_id"),
            ForeignKey("agent_id", "service_agents", "agent_id"),
            ForeignKey("interaction_id", "call_center_interactions", "interaction_id"),
        ),
    ),
    TableSpec(
        name="digital_events",
        kind=TableKind.FACT,
        layout=Layout.DAILY_PARTITIONS,
        expected_rows=10_000_000,
        primary_key=("event_id",),
        event_time_column="event_date",
        columns=(
            Column("event_id", "VARCHAR(30)", nullable=False),
            Column("event_date", "TIMESTAMP", nullable=False),
            Column("process_date", "DATE", nullable=False),
            Column("customer_id", "VARCHAR(20)", nullable=True),
            Column("session_id", "VARCHAR(50)", nullable=False),
            Column("event_type", "VARCHAR(50)", nullable=True),
            Column("event_category", "VARCHAR(50)", nullable=False),
            Column("channel", "VARCHAR(30)", nullable=False),
            Column("platform", "VARCHAR(30)", nullable=True),
            Column("browser", "VARCHAR(50)", nullable=True),
            Column("app_version", "VARCHAR(20)", nullable=True),
            Column("page_url", "VARCHAR(300)", nullable=True),
            Column("page_title", "VARCHAR(200)", nullable=True),
            Column("action", "VARCHAR(100)", nullable=True),
            Column("element_id", "VARCHAR(100)", nullable=True),
            Column("product_id", "VARCHAR(20)", nullable=True),
            Column("event_value", "DECIMAL(15,2)", nullable=True),
            Column("duration_seconds", "INTEGER", nullable=True),
            Column("ip_address", "VARCHAR(45)", nullable=True),
            Column("ip_country", "VARCHAR(50)", nullable=True),
            Column("ip_city", "VARCHAR(100)", nullable=True),
            Column("is_mobile", "BOOLEAN", nullable=False),
            Column("referrer", "VARCHAR(300)", nullable=True),
            Column("utm_source", "VARCHAR(100)", nullable=True),
            Column("utm_medium", "VARCHAR(100)", nullable=True),
            Column("utm_campaign", "VARCHAR(100)", nullable=True),
        ),
        foreign_keys=(
            ForeignKey("customer_id", "customers", "customer_id"),
            ForeignKey("product_id", "products", "product_id"),
        ),
    ),
    TableSpec(
        name="complaints",
        kind=TableKind.FACT,
        layout=Layout.DAILY_PARTITIONS,
        expected_rows=80_000,
        primary_key=("complaint_id",),
        event_time_column="creation_date",
        columns=(
            Column("complaint_id", "VARCHAR(30)", nullable=False),
            Column("creation_date", "TIMESTAMP", nullable=False),
            Column("process_date", "DATE", nullable=False),
            Column("customer_id", "VARCHAR(20)", nullable=False),
            Column("case_type", "VARCHAR(30)", nullable=False),
            Column("category", "VARCHAR(100)", nullable=False),
            Column("subcategory", "VARCHAR(100)", nullable=True),
            Column("reception_channel", "VARCHAR(30)", nullable=False),
            Column("affected_product_id", "VARCHAR(20)", nullable=True),
            Column("related_branch_id", "VARCHAR(20)", nullable=True),
            Column("origin_interaction_id", "VARCHAR(30)", nullable=True),
            Column("description", "TEXT", nullable=False),
            Column("claimed_amount", "DECIMAL(15,2)", nullable=True),
            Column("currency", "VARCHAR(3)", nullable=True),
            Column("priority", "VARCHAR(20)", nullable=False),
            Column("status", "VARCHAR(30)", nullable=True),
            Column("assigned_agent_id", "VARCHAR(20)", nullable=True),
            Column("assignment_date", "TIMESTAMP", nullable=True),
            Column("first_response_date", "TIMESTAMP", nullable=True),
            Column("resolution_date", "TIMESTAMP", nullable=True),
            Column("closing_date", "TIMESTAMP", nullable=True),
            Column("sla_breached", "BOOLEAN", nullable=False),
            Column("resolution_days", "INTEGER", nullable=True),
            Column("resolution", "TEXT", nullable=True),
            Column("compensation_granted", "DECIMAL(15,2)", nullable=True),
            Column("resolution_satisfaction", "INTEGER", nullable=True),
            Column("is_repeat_complainer", "BOOLEAN", nullable=False),
        ),
        foreign_keys=(
            ForeignKey("customer_id", "customers", "customer_id"),
            ForeignKey("assigned_agent_id", "service_agents", "agent_id"),
            ForeignKey("affected_product_id", "products", "product_id"),
            ForeignKey("related_branch_id", "branches", "branch_id"),
            ForeignKey("origin_interaction_id", "call_center_interactions", "interaction_id"),
        ),
    ),
    TableSpec(
        name="campaign_sends",
        kind=TableKind.FACT,
        layout=Layout.DAILY_PARTITIONS,
        expected_rows=2_000_000,
        primary_key=("send_id",),
        event_time_column="send_date",
        columns=(
            Column("send_id", "VARCHAR(30)", nullable=False),
            Column("send_date", "TIMESTAMP", nullable=False),
            Column("process_date", "DATE", nullable=False),
            Column("campaign_id", "VARCHAR(20)", nullable=False),
            Column("customer_id", "VARCHAR(20)", nullable=False),
            Column("send_channel", "VARCHAR(30)", nullable=False),
            Column("template_used", "VARCHAR(100)", nullable=True),
            Column("subject", "VARCHAR(200)", nullable=True),
            Column("send_status", "VARCHAR(20)", nullable=False),
            Column("was_delivered", "BOOLEAN", nullable=False),
            Column("was_opened", "BOOLEAN", nullable=True),
            Column("open_date", "TIMESTAMP", nullable=True),
            Column("was_clicked", "BOOLEAN", nullable=True),
            Column("click_date", "TIMESTAMP", nullable=True),
            Column("click_count", "INTEGER", nullable=True),
            Column("had_conversion", "BOOLEAN", nullable=False),
            Column("conversion_date", "TIMESTAMP", nullable=True),
            Column("conversion_value", "DECIMAL(15,2)", nullable=True),
            Column("open_device", "VARCHAR(30)", nullable=True),
            Column("open_country", "VARCHAR(50)", nullable=True),
            Column("failure_reason", "VARCHAR(200)", nullable=True),
            Column("send_cost", "DECIMAL(10,4)", nullable=True),
        ),
        foreign_keys=(
            ForeignKey("customer_id", "customers", "customer_id"),
            ForeignKey("campaign_id", "marketing_campaigns", "campaign_id"),
        ),
    ),
    TableSpec(
        name="daily_exchange_rates",
        kind=TableKind.REFERENCE,
        layout=Layout.SINGLE_FILE,
        expected_rows=3_000,
        primary_key=("date", "source_currency", "target_currency"),
        event_time_column=None,
        columns=(
            Column("date", "DATE", nullable=False),
            Column("source_currency", "VARCHAR(3)", nullable=False),
            Column("target_currency", "VARCHAR(3)", nullable=False),
            Column("exchange_rate", "DECIMAL(12,6)", nullable=False),
            Column("buy_rate", "DECIMAL(12,6)", nullable=True),
            Column("sell_rate", "DECIMAL(12,6)", nullable=True),
            Column("source", "VARCHAR(50)", nullable=True),
        ),
        foreign_keys=(),
    ),
)


def table(name: str) -> TableSpec:
    """Return the spec for ``name``.

    Raises
    ------
    KeyError
        When no table with that name is registered.
    """
    for spec in TABLES:
        if spec.name == name:
            return spec
    raise KeyError(name)
