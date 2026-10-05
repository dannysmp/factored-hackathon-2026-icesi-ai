-- =============================================================================
-- Migration 0017 — the transactions offered in a list, kept on the dialogue state
-- =============================================================================
-- When a customer asks to see their transactions the reply numbers them, and a later "the second
-- one" has to resolve to the transaction shown in that position. The references are kept in the
-- order they were shown (at most five, the size of a list page) and emptied once one is selected.

ALTER TABLE dialogue_state
    ADD COLUMN offered_refs TEXT[] NOT NULL DEFAULT '{}'
        CHECK (cardinality(offered_refs) <= 5);
