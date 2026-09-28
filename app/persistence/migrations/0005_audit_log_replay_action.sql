-- Adds 'case_creation_replayed' to audit_log's action CHECK constraint (0003 is frozen and never
-- edited to add a value, per its own header comment): a repeated confirmation with the same
-- idempotency key and payload now gets its own audit record, distinct from the original
-- case_created, so a trace built from audit records alone accounts for every filing call the
-- customer actually made, not only the one that inserted a row.

ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_check;

ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_check CHECK (action IN (
    'transactions_listed', 'transaction_viewed', 'transaction_probed',
    'cases_listed', 'case_viewed', 'case_probed',
    'dispute_evaluated', 'case_created', 'case_creation_refused', 'case_creation_replayed'
));
