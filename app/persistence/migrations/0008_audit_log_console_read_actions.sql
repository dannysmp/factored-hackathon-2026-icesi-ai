-- Adds 'packet_viewed' and 'timeline_viewed' to audit_log's action CHECK constraint (0003 is
-- frozen and never edited to add a value, per its own header comment): an agent opening a handoff
-- packet or a conversation's audit timeline is a customer-data access like any other (ADR-17), so
-- it is audited under the same fail-closed rule as every other read this table already records.

ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_check;

ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_check CHECK (action IN (
    'transactions_listed', 'transaction_viewed', 'transaction_probed',
    'cases_listed', 'case_viewed', 'case_probed',
    'dispute_evaluated', 'case_created', 'case_creation_refused', 'case_creation_replayed',
    'packet_viewed', 'timeline_viewed'
));
