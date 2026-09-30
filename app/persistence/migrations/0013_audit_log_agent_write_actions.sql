-- =============================================================================
-- Migration 0013 — the narrow agent writes join the audit trail
-- =============================================================================
-- ticket_claimed, ticket_released, ticket_note_added and case_status_set are the four narrow,
-- audited agent writes (ADR-17): claim or release a ticket, add a note, set a case's
-- status. Each is a brand-new action with zero pre-existing rows, unlike packet_viewed/
-- timeline_viewed (0011), so — unlike those two — agent_id can be a hard requirement for these
-- four from day one, without risking a constraint no existing row could satisfy.

ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_check;

ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_check CHECK (action IN (
    'transactions_listed', 'transaction_viewed', 'transaction_probed',
    'cases_listed', 'case_viewed', 'case_probed',
    'dispute_evaluated', 'case_created', 'case_creation_refused', 'case_creation_replayed',
    'packet_viewed', 'timeline_viewed',
    'ticket_claimed', 'ticket_released', 'ticket_note_added', 'case_status_set'
));

ALTER TABLE audit_log ADD CONSTRAINT audit_log_agent_write_requires_agent_id CHECK (
    action NOT IN ('ticket_claimed', 'ticket_released', 'ticket_note_added', 'case_status_set')
    OR agent_id IS NOT NULL
);
