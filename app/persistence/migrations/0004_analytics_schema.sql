-- The BI dashboard's read surface (ADR-11): Metabase reads these tables through the read-only
-- analytics_reader role and never the operational schema. Structure mirrors pipelines.gold's
-- seven dispute-demand marts exactly (frozen there, ordered by each mart's own key); a changed
-- mart shape is a new, later-numbered migration, never an edit to this one, matching every other
-- migration in this project.
--
-- analytics_reader has USAGE and SELECT on this schema (and, by default privilege, on any table
-- added to it later) but no grant anywhere else, so it cannot read the operational tables
-- migration 0001 created even if a future analytics table forgets to grant it explicitly. It has
-- no password yet: Metabase's own provisioning slice sets one when it configures the connection,
-- so the schema and its grants exist and are already correct well before anything can log in
-- as this role.

CREATE SCHEMA analytics;

CREATE TABLE analytics.dispute_cases_monthly (
    month DATE NOT NULL,
    cases BIGINT NOT NULL,
    closed_cases BIGINT NOT NULL,
    escalated_cases BIGINT NOT NULL,
    rejected_cases BIGINT NOT NULL,
    sla_breached_cases BIGINT NOT NULL,
    repeat_complainer_cases BIGINT NOT NULL,
    first_response_cases BIGINT NOT NULL
);

CREATE TABLE analytics.dispute_resolution (
    status TEXT NOT NULL,
    cases BIGINT NOT NULL,
    cases_with_days BIGINT NOT NULL,
    days_sum DOUBLE PRECISION,
    median_days DOUBLE PRECISION,
    p90_days DOUBLE PRECISION,
    sla_breached_cases BIGINT NOT NULL
);

CREATE TABLE analytics.dispute_resolution_overall (
    cases_with_days BIGINT NOT NULL,
    median_days DOUBLE PRECISION,
    p90_days DOUBLE PRECISION
);

CREATE TABLE analytics.dispute_claims_by_currency (
    currency TEXT NOT NULL,
    cases BIGINT NOT NULL,
    cases_with_amount BIGINT NOT NULL,
    claimed_total NUMERIC(38, 2)
);

CREATE TABLE analytics.complaint_category_mix (
    category TEXT NOT NULL,
    subcategory TEXT NOT NULL,
    cases BIGINT NOT NULL
);

CREATE TABLE analytics.contact_demand_monthly (
    month DATE NOT NULL,
    reason_category TEXT NOT NULL,
    interactions BIGINT NOT NULL,
    interactions_with_duration BIGINT NOT NULL,
    duration_seconds_sum DOUBLE PRECISION,
    interactions_with_wait BIGINT NOT NULL,
    wait_seconds_sum DOUBLE PRECISION,
    resolved_interactions BIGINT NOT NULL,
    escalated_interactions BIGINT NOT NULL,
    followup_interactions BIGINT NOT NULL,
    neutral_interactions BIGINT NOT NULL,
    negative_interactions BIGINT NOT NULL,
    interactions_with_score BIGINT NOT NULL,
    sentiment_score_sum NUMERIC(38, 2)
);

CREATE TABLE analytics.contact_satisfaction (
    reason_category TEXT NOT NULL,
    surveys BIGINT NOT NULL,
    surveys_with_score BIGINT NOT NULL,
    score_sum DOUBLE PRECISION
);

CREATE ROLE analytics_reader NOLOGIN;
GRANT USAGE ON SCHEMA analytics TO analytics_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO analytics_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO analytics_reader;
