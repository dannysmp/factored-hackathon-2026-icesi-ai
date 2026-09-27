-- Two additions for the case-service write path, both on the already-shipped ``cases`` table
-- (0001, frozen); neither is an edit to that file.
--
-- 1. 0001 left ``reason_code`` without a CHECK constraint, unlike every other closed-set column
--    in that table. The closed set copies app.domain.policy.models.ReasonCode in full, not a
--    narrowed subset of "eligible" reason codes: 0001's own stated principle is that a closed-set
--    CHECK constraint copies the frozen source values, "never a guessed or narrowed subset".
--    Eligibility itself is enforced only by the controller and checked only by the independent
--    oracle (AC-E4-39, ADR-3); narrowing this constraint to eligible-only reason codes would
--    plant a second, driftable copy of that rule at the schema layer.
--
-- 2. A unique open case per transaction (ADR-3's "duplicate open case" permission invariant),
--    enforced at the store, not only by the create tool's own check-before-insert: a partial
--    unique index over the two open statuses is the DB-level safety net for the race the tool's
--    own proactive check cannot fully close.
--
-- Both validate existing rows (no NOT VALID): nothing writes cases yet, so no data can fail them.

ALTER TABLE cases ADD CONSTRAINT cases_reason_code_check CHECK (
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
);

CREATE UNIQUE INDEX cases_transaction_id_open_unique ON cases (transaction_id)
    WHERE status IN ('Open', 'In Review');
