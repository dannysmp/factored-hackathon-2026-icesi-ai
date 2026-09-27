-- =============================================================================
-- Migration 0001 — serving store
-- =============================================================================
-- The tools read one store of customers, transactions, products and cases (ADR-9). This
-- migration creates that store's four tables. Frozen once shipped: a later change is a new,
-- separate migration file, never an edit to this one.
--
-- Columns mirror the source tables (pipelines/sources.py) minus what the seed must never carry:
-- no document number, birth date, address, full email or full phone. A masked email and phone
-- are kept for what a rendered reply may reference; the seed populates them already masked.
--
-- Closed-set CHECK constraints copy the frozen source values from contracts/v1.py's
-- AllowedValues (customer_status, product_status, transaction_type, transaction_status), never a
-- guessed or narrowed subset, plus the closed sets contracts/service_v1 and app.domain.policy
-- already freeze for the service's own vocabulary (case status, dispute category, amount
-- provenance, language).
--
-- Sessions, turns and the audit log belong to a later, separate migration; this one holds only
-- the four tables above.
-- =============================================================================

CREATE TABLE customers (
    customer_id VARCHAR(20) PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    masked_email VARCHAR(100),
    masked_phone VARCHAR(20),
    country VARCHAR(50) NOT NULL,
    customer_status VARCHAR(20) NOT NULL
        CHECK (customer_status IN ('Active', 'Inactive', 'Suspended', 'Closed'))
);

CREATE TABLE products (
    product_id VARCHAR(20) PRIMARY KEY,
    customer_id VARCHAR(20) NOT NULL REFERENCES customers (customer_id),
    product_type VARCHAR(50),
    last4 CHAR(4) NOT NULL CHECK (last4 ~ '^[0-9]{4}$'),
    product_status VARCHAR(20) NOT NULL
        CHECK (product_status IN ('Active', 'Closed', 'Blocked', 'Suspended'))
);

CREATE INDEX products_customer_id_idx ON products (customer_id);

CREATE TABLE transactions (
    transaction_id VARCHAR(30) PRIMARY KEY,
    customer_id VARCHAR(20) NOT NULL REFERENCES customers (customer_id),
    product_id VARCHAR(20) NOT NULL REFERENCES products (product_id),
    transaction_date TIMESTAMP NOT NULL,
    transaction_type VARCHAR(50)
        CHECK (
            transaction_type IN (
                'Purchase', 'Withdrawal', 'Transfer', 'Payment', 'Deposit', 'Adjustment'
            )
        ),
    merchant_name VARCHAR(150),
    amount NUMERIC(15, 2) NOT NULL,
    currency CHAR(3) NOT NULL,
    amount_usd NUMERIC(15, 2),
    amount_usd_provenance VARCHAR(10) NOT NULL
        CHECK (amount_usd_provenance IN ('reported', 'converted', 'unknown')),
    transaction_status VARCHAR(20) NOT NULL
        CHECK (transaction_status IN ('Approved', 'Declined', 'Pending', 'Reversed')),
    CONSTRAINT transactions_amount_usd_matches_provenance CHECK (
        (amount_usd_provenance = 'unknown' AND amount_usd IS NULL)
        OR (amount_usd_provenance != 'unknown' AND amount_usd IS NOT NULL)
    )
);

CREATE INDEX transactions_customer_id_idx ON transactions (customer_id);

CREATE TABLE cases (
    case_number VARCHAR(32) PRIMARY KEY,
    customer_id VARCHAR(20) NOT NULL REFERENCES customers (customer_id),
    transaction_id VARCHAR(30) NOT NULL REFERENCES transactions (transaction_id),
    session_id VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(32) NOT NULL,
    status VARCHAR(20) NOT NULL
        CHECK (status IN ('Open', 'In Review', 'Resolved', 'Rejected')),
    category VARCHAR(50) NOT NULL
        CHECK (
            category IN (
                'unrecognized_charge', 'duplicate_charge', 'wrong_amount',
                'service_not_received', 'fraud_claim'
            )
        ),
    amount NUMERIC(15, 2),
    currency CHAR(3),
    amount_provenance VARCHAR(10) NOT NULL
        CHECK (amount_provenance IN ('reported', 'converted', 'unknown')),
    domain_date DATE NOT NULL,
    expected_first_response_date DATE NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL,
    policy_version VARCHAR(16) NOT NULL,
    reason_code VARCHAR(64) NOT NULL,
    language VARCHAR(2) NOT NULL CHECK (language IN ('es', 'pt', 'en')),
    CONSTRAINT cases_amount_matches_provenance CHECK (
        (amount_provenance = 'unknown' AND amount IS NULL AND currency IS NULL)
        OR (amount_provenance != 'unknown' AND amount IS NOT NULL AND currency IS NOT NULL)
    ),
    CONSTRAINT cases_response_not_before_domain_date
        CHECK (expected_first_response_date >= domain_date),
    CONSTRAINT cases_customer_idempotency_key_unique UNIQUE (customer_id, idempotency_key)
);

CREATE INDEX cases_customer_id_idx ON cases (customer_id);
CREATE INDEX cases_transaction_id_idx ON cases (transaction_id);
