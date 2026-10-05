-- =============================================================================
-- Migration 0016 — the model spend recorded per operating day
-- =============================================================================
-- One row per operating day (the bank's own calendar day, America/Bogota) holding the dollars
-- spent on model calls that day. The daily spend breaker reads today's row before each call and
-- adds the call's cost after it; the row is created by the first charge of a day.

CREATE TABLE llm_spend_daily (
    spend_day DATE PRIMARY KEY,
    spent_usd NUMERIC(12, 6) NOT NULL CHECK (spent_usd >= 0)
);
