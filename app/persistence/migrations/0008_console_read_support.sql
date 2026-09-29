-- =============================================================================
-- Migration 0008 — console read support: the turn-history timeline and ticket status
-- =============================================================================
-- Two additive pieces the human-agent console's read side needs (ADR-17), neither of which
-- extends stream 1's frozen audit_log:
--
-- 1. dialogue_turn_log: one row per turn the controller actually advances (never a replay — a
--    repeated turn id changes nothing, so it writes nothing here either), backing
--    contracts/service_v1/console.py's TimelineEntry. audit_log (migration 0003) is one row per
--    tool call and cannot back this: a turn can carry zero, one or several tool calls, and
--    intent/state_before/state_after/render_mode are properties of the turn, not of any one call
--    within it. Its own append-only trigger mirrors 0003's, deliberately not reused across tables.
--    UNIQUE (session_id, turn_id) mirrors handoff_outbox's own dedup key (0006) for the same
--    reason: a retried turn must never double an entry.
--
-- 2. handoff_outbox.status: the four-value ticket lifecycle contracts/service_v1/console.py's
--    TicketStatus names — the same four states 0001's cases.status already carries, stored here in
--    TicketStatus's own lowercase spelling (this column round-trips through that contract, not
--    through cases.status's separate Title-Case one). Until the console's own agent writes ship
--    (ADR-17: "the mock case lifecycle is advanced by the seed until the writes arrive"), the seed
--    is this column's source of truth.
-- =============================================================================

CREATE TABLE dialogue_turn_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    turn_id VARCHAR(64) NOT NULL,
    trace_id VARCHAR(32) NOT NULL,
    occurred_at_utc TIMESTAMPTZ NOT NULL,
    intent VARCHAR(24) NOT NULL
        CHECK (
            intent IN (
                'clarify', 'present_transactions', 'confirm_filing', 'filing_result',
                'ineligible', 'dispute_status', 'policy_answer', 'abstain', 'refuse',
                'handoff', 'farewell'
            )
        ),
    state_before VARCHAR(16) NOT NULL
        CHECK (
            state_before IN
                ('started', 'clarifying', 'confirming', 'closed', 'handed_off', 'abandoned')
        ),
    state_after VARCHAR(16) NOT NULL
        CHECK (
            state_after IN
                ('started', 'clarifying', 'confirming', 'closed', 'handed_off', 'abandoned')
        ),
    render_mode VARCHAR(8) NOT NULL CHECK (render_mode IN ('template', 'model')),
    reason_code VARCHAR(64)
        CHECK (
            reason_code IN (
                'eligible',
                'product_out_of_scope',
                'transaction_type_not_disputable',
                'transaction_declined',
                'transaction_pending',
                'transaction_reversed',
                'transaction_date_in_future',
                'filing_window_expired',
                'duplicate_open_case',
                'escalate_fraud_claim',
                'escalate_low_nlu_confidence',
                'escalate_repeat_complainer',
                'escalate_amount_above_threshold',
                'escalate_amount_unknown',
                'escalate_risk_score'
            )
        ),
    policy_version VARCHAR(16),
    CONSTRAINT dialogue_turn_log_session_turn_unique UNIQUE (session_id, turn_id)
);

CREATE INDEX dialogue_turn_log_trace_id_idx ON dialogue_turn_log (trace_id, occurred_at_utc);

CREATE FUNCTION dialogue_turn_log_forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'dialogue_turn_log is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER dialogue_turn_log_append_only
    BEFORE UPDATE OR DELETE ON dialogue_turn_log
    FOR EACH ROW EXECUTE FUNCTION dialogue_turn_log_forbid_mutation();

CREATE TRIGGER dialogue_turn_log_forbid_truncate
    BEFORE TRUNCATE ON dialogue_turn_log
    FOR EACH STATEMENT EXECUTE FUNCTION dialogue_turn_log_forbid_mutation();

ALTER TABLE handoff_outbox
    ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'in_review', 'resolved', 'rejected'));
