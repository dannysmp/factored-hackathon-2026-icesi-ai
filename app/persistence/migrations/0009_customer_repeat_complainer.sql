-- =============================================================================
-- Migration 0009 — customers.is_repeat_complainer: the escalation matrix's repeat-complainer
-- signal, carried into the serving store
-- =============================================================================
-- pipelines.ops_seed already computes this fact (its own _flags_query's repeat_complainers CTE)
-- to select which customers the seed carries, but discarded it before writing customers.parquet —
-- the serving store had no column to hold it. Without this, app.persistence.reads.evaluate_dispute
-- could never pass a real value to the policy engine, so escalate_repeat_complainer (AC-E4-43) was
-- unreachable in the running service regardless of a seeded customer's real complaint history.
-- =============================================================================

ALTER TABLE customers ADD COLUMN is_repeat_complainer BOOLEAN NOT NULL DEFAULT false;
