-- =============================================================================
-- Migration 0002 — operational metadata
-- =============================================================================
-- A small key-value table for facts about the loaded seed that the running service reads at
-- start-up, not facts about a customer, transaction or case. Its first and only key today is
-- data_as_of (ADR-15): the operating-zone date of the newest transaction instant in the seed,
-- read by the DomainCalendar when DATA_AS_OF_DATE does not override it.
-- =============================================================================

CREATE TABLE ops_meta (
    key VARCHAR(50) PRIMARY KEY,
    value TEXT NOT NULL
);
