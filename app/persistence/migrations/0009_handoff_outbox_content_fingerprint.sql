-- =============================================================================
-- Migration 0009 — a stored content fingerprint for the handoff outbox's replay check
-- =============================================================================
-- app/persistence/handoff_outbox.py's replay comparison used to re-read every persisted scalar
-- column and all four child tables and compare them field by field against the caller's fresh
-- HandoffContent — a list that had to be extended by hand each time HandoffContent grew, and had
-- already drifted out of sync once. This column stores a SHA-256 digest over HandoffContent's own
-- fields instead, computed once at insert and compared once at replay, so a field added to
-- HandoffContent later is covered automatically with no matching edit here.
--
-- Nullable, no default: a row written before this migration has no fingerprint to compare, and
-- none can be reconstructed after the fact — the digest covers fields this table never persisted
-- in the first place (a full TransactionFact, not just its stored reference). A NULL is never
-- treated as a trusted replay; the application layer raises the same HandoffReplayMismatch it
-- raises for a genuine disagreement, since an unverifiable row is not a verified one. Every row
-- _insert writes from this migration on always supplies a real fingerprint — enforced by the
-- application, not a NOT NULL constraint, so an old row's own NULL stays representable.

ALTER TABLE handoff_outbox ADD COLUMN content_fingerprint VARCHAR(64);
