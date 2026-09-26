# Data Quality Report

Contract version 1. Every figure is computed by `make pipeline` from the manifests of the cleaned layer, so the report describes the state of the data and not the actions of the last run. A row is either kept, superseded by a newer version of the same key, or quarantined with a reason.

## 1. Outcome per table

| Table | Rows in | Rows out | Superseded | Quarantined | Output digest |
|---|---|---|---|---|---|
| branches | 350 | 350 | 0 | 0 | ecc678c97d61 |
| daily_exchange_rates | 13,164 | 13,164 | 0 | 0 | c385e6d4f565 |
| marketing_campaigns | 200 | 200 | 0 | 0 | 7a6f035295f4 |
| customers | 150,000 | 150,000 | 0 | 0 | 8fe5b47bc922 |
| service_agents | 1,200 | 1,200 | 0 | 0 | 7f76d7934b00 |
| call_center_interactions | 686,296 | 686,296 | 0 | 0 | 03b5a57f9fce |
| campaign_sends | 1,746,801 | 1,746,801 | 0 | 0 | 0193ba68a47b |
| products | 400,000 | 400,000 | 0 | 0 | ba69f7bc2f26 |
| call_transcripts | 171,321 | 147,292 | 0 | 24,029 | 8d1076ee76cb |
| complaints | 67,095 | 67,095 | 0 | 0 | d2cc9a6f7bfd |
| digital_events | 15,620,994 | 15,620,994 | 0 | 0 | b817460ee8f7 |
| satisfaction_surveys | 212,759 | 212,759 | 0 | 0 | 6e8d6b75fac2 |
| transactions | 4,425,008 | 4,425,008 | 0 | 0 | 3c7933a5f82e |

## 2. Quarantine reasons

Reason codes are `required` (missing value in a required column), `type` (does not parse as the declared type), `value` (outside the allowed values), `range` (outside the numeric range) and `reference` (points at no existing row).

| Table | Reason | Rows |
|---|---|---|
| call_transcripts | required:duration_seconds | 24,029 |

## 3. References

| Table | Column | Handling | Rows |
|---|---|---|---|
| customers | registration_branch_id | flagged | 149,995 |
| service_agents | assigned_branch_id | flagged | 831 |
| call_center_interactions | agent_id | flagged | 0 |
| products | opening_branch_id | flagged | 0 |
| complaints | affected_product_id | flagged | 0 |
| complaints | assigned_agent_id | flagged | 0 |
| complaints | origin_interaction_id | flagged | 0 |
| complaints | related_branch_id | flagged | 0 |
| digital_events | customer_id | flagged | 0 |
| digital_events | product_id | flagged | 0 |
| satisfaction_surveys | agent_id | flagged | 0 |
| satisfaction_surveys | interaction_id | flagged | 0 |
| transactions | branch_id | flagged | 0 |

## 4. Columns not in the contract

| Table | Columns |
|---|---|
| none |  |

## 5. Tables not processed

| Table | Reason |
|---|---|
| none |  |
