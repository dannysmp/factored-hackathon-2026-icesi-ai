-- =============================================================================
-- Migration 0014 — who currently holds a ticket
-- =============================================================================
-- claimed_by/claimed_at track a ticket's current claim, the first of the narrow agent writes. This
-- pairs with, and is orthogonal to, handoff_outbox.status (0008): status is the ticket's own
-- lifecycle stage (open/in_review/resolved/rejected, still seed-advanced until the case-status
-- write below), while a claim is only "which agent is currently working this ticket," released or
-- reassigned independently of status. handoff_outbox already takes an in-place UPDATE for
-- read_at (0006) — this table is not append-only, unlike audit_log/signin_audit — so a claim is
-- the table's second mutable, current-state column family, not its first.

ALTER TABLE handoff_outbox ADD COLUMN claimed_by VARCHAR(20);
ALTER TABLE handoff_outbox ADD COLUMN claimed_at TIMESTAMPTZ;

ALTER TABLE handoff_outbox ADD CONSTRAINT handoff_outbox_claim_together CHECK (
    (claimed_by IS NULL) = (claimed_at IS NULL)
);
