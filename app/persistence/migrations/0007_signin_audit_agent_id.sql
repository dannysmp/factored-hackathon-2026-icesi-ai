-- The agent demo sign-in broker (ADR-17, ADR-18) resolves an agent persona to an agent_id, not a
-- customer_id: reusing signin_audit's resolved_customer_id column for it would store an agent's
-- identifier under a column whose meaning everywhere else is "the customer this attempt
-- resolved" (and would collide with the append-only table's own additive-only history). This adds
-- a second, equally optional column and a constraint pairing each with its own audience, so a
-- customer attempt can never carry an agent id or the reverse.

ALTER TABLE signin_audit ADD COLUMN resolved_agent_id VARCHAR(20);

ALTER TABLE signin_audit ADD CONSTRAINT signin_audit_resolved_id_matches_audience CHECK (
    (audience = 'customer' AND resolved_agent_id IS NULL)
    OR (audience = 'agent' AND resolved_customer_id IS NULL)
);
