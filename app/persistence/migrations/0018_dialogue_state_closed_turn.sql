-- =============================================================================
-- Migration 0018 — the turn that closed a dispute without filing a case
-- =============================================================================
-- A repeated turn id is answered again from the saved state. A session that filed a case keeps
-- its number, so without this marker the repeat of a later turn that cancelled or refused another
-- dispute could not be told from the repeat of the filing turn and would report the earlier case.
-- The column holds the id of the turn that closed a dispute without filing; it is empty until one
-- does, and it is compared with the id of the turn being repeated.

ALTER TABLE dialogue_state
    ADD COLUMN closed_turn_id VARCHAR(64);
