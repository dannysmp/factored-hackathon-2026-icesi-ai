-- =============================================================================
-- Migration 0015 — an agent's own notes on a ticket
-- =============================================================================
-- handoff_notes follows the same (ticket_ref, ord) shape handoff_actions/handoff_open_questions/
-- handoff_reason_codes already use for a ticket's bounded repeating parts (0006). Insert-only from
-- the application; it needs no append-only trigger of its own the way audit_log/signin_audit do —
-- each note-add is already, independently and immutably recorded in audit_log via
-- ticket_note_added (0013), which is the compliance trail. This table is the note's own current
-- home for the console to read back, not the record of the fact that it was added.

CREATE TABLE handoff_notes (
    ticket_ref VARCHAR(32) NOT NULL REFERENCES handoff_outbox (ticket_ref),
    ord INT NOT NULL CHECK (ord >= 0),
    agent_id VARCHAR(20) NOT NULL,
    note_text VARCHAR(500) NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (ticket_ref, ord)
);
