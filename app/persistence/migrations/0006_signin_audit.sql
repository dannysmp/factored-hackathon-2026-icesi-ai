-- Sign-in audit for the demo broker (ADR-18): a separate append-only trail from audit_log
-- (0003, frozen), because a sign-in attempt is not a tool call inside an established session —
-- the thing audit_log's own schema requires (customer_id, session_id both NOT NULL). A sign-in
-- attempt structurally has neither at the moment it must be audited: ADR-18 requires the record
-- written *before* the token is returned, so even a successful attempt has no session_id yet at
-- that point, and a refused attempt (wrong access code, unknown persona) may never resolve a
-- real customer at all. Reusing audit_log would mean fabricating values in columns whose only
-- present meaning, everywhere else they appear, is "the session under which this happened."
--
-- Append-only, enforced here: reuses 0003's audit_log_forbid_mutation() trigger function rather
-- than redefining it, since the guarantee it enforces (refuse UPDATE/DELETE/TRUNCATE regardless
-- of role) is table-agnostic.

CREATE TABLE signin_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trace_id VARCHAR(32) NOT NULL,
    occurred_at_utc TIMESTAMPTZ NOT NULL,
    audience VARCHAR(16) NOT NULL CHECK (audience IN ('customer', 'agent')),
    persona_slug VARCHAR(64),
    resolved_customer_id VARCHAR(20),
    client_address_hash CHAR(64) NOT NULL CHECK (client_address_hash ~ '^[0-9a-f]{64}$'),
    outcome VARCHAR(16) NOT NULL CHECK (outcome IN ('issued', 'refused')),
    reason_code VARCHAR(32) NOT NULL
        CHECK (
            reason_code IN (
                'issued', 'invalid_access_code', 'unknown_persona',
                'inactive_customer', 'rate_limited'
            )
        ),
    session_id VARCHAR(64),
    CONSTRAINT signin_audit_session_id_matches_outcome CHECK (
        (outcome = 'issued' AND session_id IS NOT NULL)
        OR (outcome = 'refused' AND session_id IS NULL)
    )
);
CREATE INDEX signin_audit_trace_id_idx ON signin_audit (trace_id);

CREATE TRIGGER signin_audit_append_only
    BEFORE UPDATE OR DELETE ON signin_audit
    FOR EACH ROW EXECUTE FUNCTION audit_log_forbid_mutation();

CREATE TRIGGER signin_audit_forbid_truncate
    BEFORE TRUNCATE ON signin_audit
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_forbid_mutation();
