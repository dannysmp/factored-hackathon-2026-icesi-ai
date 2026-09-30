-- =============================================================================
-- Migration 0011 — an agent's own identity, alongside the session that carried an action
-- =============================================================================
-- ADR-17 requires every agent read or write to be audited with the agent's own identity, not
-- only the session that carried it: a session is ephemeral (the agent sign-in broker's revocation
-- store is in-memory, ADR-18), so audit_log alone could not answer "which agent read this packet"
-- once a session expires. AuditRecord had no field for it and PostgresConsoleAuditSink discarded
-- the agent_id it was already handed, matching every other action's shape rather than an agent's.
--
-- Nullable, no CHECK requiring it yet: audit_log is append-only (0003's own trigger forbids
-- UPDATE/DELETE regardless of role), so a row already written for packet_viewed/timeline_viewed
-- before this migration can never gain an agent_id after the fact. The application populates it
-- for every new agent-attributed row from this point on; a hard NOT NULL-by-action constraint,
-- where it is warranted, is added alongside the action it actually applies to, not retrofitted
-- onto an action whose existing rows could never satisfy it. Sized like signin_audit's own
-- resolved_agent_id (0007): VARCHAR(20), matching AuditRecord.customer_id's own width.

ALTER TABLE audit_log ADD COLUMN agent_id VARCHAR(20);
