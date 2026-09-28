-- =============================================================================
-- Migration 0006 — dialogue state and the handoff outbox
-- =============================================================================
-- Two independent stores for the conversation layer:
--
-- 1. dialogue_state: one row per session, mirroring app.conversation.state.DialogueState. The
--    primary key is session_id itself (a session's dialogue state is looked up by nothing else),
--    and last_turn_id/last_case_number/last_ticket_ref exist for a repeated turn id to answer from
--    the current record rather than a cached reply.
--
-- 2. handoff_outbox and its four child tables: the local outbox row the architecture document
--    requires so a handoff never depends on the model, retrieval or any other tool being up. A
--    handoff is written before the turn's dialogue-state save, on its own connection, so a crash
--    or a version conflict afterward never loses an already-committed handoff. verified_facts and
--    evidence.sources are not duplicated here: a transaction is re-resolved from
--    verified_transaction_ref and a source's title from section_id + corpus_version at read time,
--    the same one-figure-one-source discipline 0001's cases table already follows for its own
--    facts. needs_language_routing is not stored either: contracts.service_v1.handoff.HandoffPacket
--    already derives it from language != 'es'.
--
-- Closed-set CHECK constraints copy the frozen enum values verbatim (never a guessed or narrowed
-- subset, matching 0001's and 0004's own stated rule): language and category from
-- contracts.service_v1.envelope, phase and pending_slot from app.conversation.state, trigger from
-- contracts.service_v1.handoff.HandoffTrigger, reason_code from app.domain.policy.models.ReasonCode
-- (the same list 0004 already put on cases.reason_code).
-- =============================================================================

CREATE TABLE dialogue_state (
    session_id VARCHAR(64) PRIMARY KEY,
    version INT NOT NULL CHECK (version >= 1),
    lang VARCHAR(2) NOT NULL CHECK (lang IN ('es', 'pt', 'en')),
    phase VARCHAR(16) NOT NULL
        CHECK (phase IN ('started', 'clarifying', 'confirming', 'closed', 'handed_off', 'abandoned')),
    pending_slot VARCHAR(24)
        CHECK (pending_slot IN ('transaction', 'transaction_choice', 'reason', 'confirmation')),
    clarification_attempts INT NOT NULL CHECK (clarification_attempts >= 0),
    category VARCHAR(32)
        CHECK (
            category IN (
                'unrecognized_charge', 'duplicate_charge', 'wrong_amount',
                'service_not_received', 'fraud_claim'
            )
        ),
    selected_ref VARCHAR(64),
    pending_disputes INT NOT NULL CHECK (pending_disputes >= 0 AND pending_disputes <= 5),
    last_turn_id VARCHAR(64),
    last_case_number VARCHAR(32),
    last_ticket_ref VARCHAR(32),
    updated_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE handoff_outbox (
    ticket_ref VARCHAR(32) PRIMARY KEY,
    customer_id VARCHAR(20) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    trace_id VARCHAR(32) NOT NULL,
    turn_id VARCHAR(64) NOT NULL,
    reference_date DATE NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL,
    language VARCHAR(2) NOT NULL CHECK (language IN ('es', 'pt', 'en')),
    trigger VARCHAR(24) NOT NULL
        CHECK (
            trigger IN (
                'fraud_report', 'card_loss', 'customer_request', 'amount_review',
                'repeat_complainer', 'risk_score', 'amount_unknown', 'low_understanding',
                'tool_failure', 'filing_unverified'
            )
        ),
    customer_first_name VARCHAR(40) NOT NULL,
    customer_masked_id VARCHAR(8) NOT NULL,
    category VARCHAR(32)
        CHECK (
            category IN (
                'unrecognized_charge', 'duplicate_charge', 'wrong_amount',
                'service_not_received', 'fraud_claim'
            )
        ),
    request_summary VARCHAR(300) NOT NULL,
    verified_transaction_ref VARCHAR(64),
    attempted_action_action VARCHAR(64),
    attempted_action_result VARCHAR(64),
    existing_case_number VARCHAR(32),
    policy_version VARCHAR(16) NOT NULL,
    risk_score DOUBLE PRECISION,
    risk_interval_low DOUBLE PRECISION,
    risk_interval_high DOUBLE PRECISION,
    risk_base_rate DOUBLE PRECISION,
    read_at TIMESTAMPTZ,
    CONSTRAINT handoff_outbox_attempted_action_together CHECK (
        (attempted_action_action IS NULL) = (attempted_action_result IS NULL)
    ),
    CONSTRAINT handoff_outbox_risk_together CHECK (
        (risk_score IS NULL) = (risk_interval_low IS NULL)
        AND (risk_score IS NULL) = (risk_interval_high IS NULL)
        AND (risk_score IS NULL) = (risk_base_rate IS NULL)
    ),
    CONSTRAINT handoff_outbox_session_turn_unique UNIQUE (session_id, turn_id)
);

CREATE INDEX handoff_outbox_session_id_idx ON handoff_outbox (session_id);
CREATE INDEX handoff_outbox_unread_idx ON handoff_outbox (created_at_utc) WHERE read_at IS NULL;

CREATE TABLE handoff_actions (
    ticket_ref VARCHAR(32) NOT NULL REFERENCES handoff_outbox (ticket_ref),
    ord INT NOT NULL CHECK (ord >= 0),
    action VARCHAR(64) NOT NULL,
    result VARCHAR(64) NOT NULL,
    PRIMARY KEY (ticket_ref, ord)
);

CREATE TABLE handoff_open_questions (
    ticket_ref VARCHAR(32) NOT NULL REFERENCES handoff_outbox (ticket_ref),
    slot VARCHAR(24) NOT NULL
        CHECK (slot IN ('transaction', 'transaction_choice', 'reason', 'confirmation')),
    attempts INT NOT NULL CHECK (attempts >= 0),
    PRIMARY KEY (ticket_ref, slot)
);

CREATE TABLE handoff_reason_codes (
    ticket_ref VARCHAR(32) NOT NULL REFERENCES handoff_outbox (ticket_ref),
    ord INT NOT NULL CHECK (ord >= 0),
    reason_code VARCHAR(64) NOT NULL
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
    PRIMARY KEY (ticket_ref, ord)
);

CREATE TABLE handoff_sources (
    ticket_ref VARCHAR(32) NOT NULL REFERENCES handoff_outbox (ticket_ref),
    ord INT NOT NULL CHECK (ord >= 0),
    section_id VARCHAR(64) NOT NULL,
    corpus_version VARCHAR(32) NOT NULL,
    PRIMARY KEY (ticket_ref, ord)
);
