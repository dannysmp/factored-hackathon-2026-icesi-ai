-- =============================================================================
-- Migration 0010 — the risk routing threshold, alongside the score it was compared to
-- =============================================================================
-- RiskEvidence now carries the policy's own routing threshold next to the score, interval and
-- base rate: a reader could otherwise see a risk score with no way to tell whether it was close
-- to, at, or nowhere near the line that would have escalated it. Risk routing is switched off
-- today (risk_routing_enabled is false), so this column is always NULL in every environment that
-- exists right now; it must land before routing is ever switched on, the same way the score,
-- interval and base rate columns already exist ahead of a caller that populates them.
--
-- Dropped and recreated handoff_outbox_risk_together (0006) rather than altering it in place:
-- Postgres has no ALTER CONSTRAINT for a CHECK's own expression, only drop-and-add.

ALTER TABLE handoff_outbox ADD COLUMN risk_threshold DOUBLE PRECISION;

ALTER TABLE handoff_outbox DROP CONSTRAINT handoff_outbox_risk_together;

ALTER TABLE handoff_outbox ADD CONSTRAINT handoff_outbox_risk_together CHECK (
    (risk_score IS NULL) = (risk_interval_low IS NULL)
    AND (risk_score IS NULL) = (risk_interval_high IS NULL)
    AND (risk_score IS NULL) = (risk_base_rate IS NULL)
    AND (risk_score IS NULL) = (risk_threshold IS NULL)
);
