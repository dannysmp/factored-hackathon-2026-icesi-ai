-- The audit trail (E4-F4): one record per user-affecting decision or read, written by
-- app.persistence.audit against contracts/service_v1/audit.py's AuditRecord. Frozen once shipped:
-- a later change is a new, separate migration file, never an edit to this one.
--
-- Append-only, enforced here, not only in code: a trigger refuses UPDATE and DELETE on this
-- table regardless of which role holds the connection, so the guarantee does not depend on a
-- hand-maintained GRANT/REVOKE list matching whatever role a deployment happens to connect as.
--
-- The closed set of actions copies contracts/service_v1/audit.py's AuditAction, frozen there;
-- a member is added, never renamed, and this file is never edited to add one — a new value needs
-- its own later migration that alters the CHECK constraint.

CREATE TABLE audit_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trace_id VARCHAR(32) NOT NULL,
    customer_id VARCHAR(20) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    action VARCHAR(32) NOT NULL
        CHECK (action IN (
            'transactions_listed', 'transaction_viewed', 'transaction_probed',
            'cases_listed', 'case_viewed', 'case_probed',
            'dispute_evaluated', 'case_created', 'case_creation_refused'
        )),
    reason_code VARCHAR(64),
    policy_version VARCHAR(16),
    tool_result_hash CHAR(64) NOT NULL CHECK (tool_result_hash ~ '^[0-9a-f]{64}$'),
    occurred_at_utc TIMESTAMPTZ NOT NULL,
    domain_date DATE NOT NULL
);
CREATE INDEX audit_log_customer_id_idx ON audit_log (customer_id);
CREATE INDEX audit_log_trace_id_idx ON audit_log (trace_id);

CREATE FUNCTION audit_log_forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_append_only
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_forbid_mutation();
