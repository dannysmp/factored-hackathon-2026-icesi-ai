# Raw Data Profile

Snapshot digest (SHA-256 over file paths and sizes): `7cd43c778552f9fcb7940643ed829ac8b6f3ac1787b01f2ed8e9e23f2b2a7b26`

Tables profiled: 13. Every figure below is computed from the raw files by `make profile`; nothing is typed in by hand. Verdicts cover only the tables that were profiled; tables that were absent or could not be parsed are listed in section 2.

## 1. Assumptions checked against the data

| Assumption | Expected | Observed | Verdict |
|---|---|---|---|
| Fact tables are partitioned as year/month/day, one file per day | No path deviates from the layout | 0 non-conforming paths; 0 calendar days without a file | Holds |
| Files are UTF-8 CSV whose header equals the dictionary's column list | Every file decodes and matches | 7,671 of 7,671 headers match; 0 undecodable; 0 without a header row; 7,671 files start with a byte-order mark | Holds |
| Row counts match the dictionary | Within 5 % of the stated figure | outside tolerance: transactions, call_center_interactions, call_transcripts, satisfaction_surveys, digital_events, complaints, campaign_sends, daily_exchange_rates | Differs |
| About 2 % of records are duplicates | 1 % to 3 % overall | 0.00 % overall; per table 0.00 % to 0.00 % | Differs |
| About 5 % of values are missing in nullable fields | 3 % to 7 % of nullable cells | 44.68 % of 384,675,615 nullable cells; median column 23.77 % | Differs |
| Only a small share of foreign keys are orphans | Every reference at most 2 % orphans (judged per reference: one broken reference invalidates joins on it whatever the overall share) | 150,826 of 30,697,924 references overall (0.49 %); worst customers.registration_branch_id → branches (100.00 %) | Differs |
| Schemas may evolve between partitions | Drift is possible and must be handled | tables with more than one header: none in this snapshot | Informational |
| Partitions can arrive after the day they describe | Some rows have a positive lag | transactions: 1,106,307 rows stamped after their partition day (event hours 0 to 6); call_center_interactions: 228,318 rows stamped after their partition day (event hours 0 to 8); satisfaction_surveys: 169,092 rows stamped after their partition day (event hours 0 to 23); digital_events: 3,930,816 rows stamped after their partition day (event hours 0 to 6); complaints: 22,585 rows stamped after their partition day (event hours 0 to 8); campaign_sends: 436,429 rows stamped after their partition day (event hours 0 to 6) | Differs |
| The fraud label supports a supervised risk model | Enough positive examples; prevalence known | 4,316 positives in 4,425,008 transactions (0.098 %) | Informational |
| amount_usd is present and consistent with the daily exchange rate | Present in at least 95 % of rows and consistent in at least 95 % | present in 42.66 % of rows; 100.00 % of 1,886,980 comparable values within tolerance | Differs |
| Transcripts exist for interactions flagged as having one | About one transcript per flagged interaction | 171,321 interactions flagged; 171,321 distinct interactions have a transcript | Informational |
| Complaint data contains dispute-like categories and a repeat-complainer signal | Categories and a repeat flag are present | 5 categories; repeat complainers 15.03 % | Informational |

## 2. Tables not profiled

| Table | Reason |
|---|---|
| none |  |

## 3. Inventory

| Table | Kind | Files | Size | Rows | Dictionary rows | Rows / dictionary | Partition span | Days without file |
|---|---|---|---|---|---|---|---|---|
| customers | dimension | 1 | 44.7 MiB | 150,000 | 150,000 | 100.0 % | single file | n/a |
| products | dimension | 1 | 65.1 MiB | 400,000 | 400,000 | 100.0 % | single file | n/a |
| branches | dimension | 1 | 0.1 MiB | 350 | 350 | 100.0 % | single file | n/a |
| service_agents | dimension | 1 | 0.2 MiB | 1,200 | 1,200 | 100.0 % | single file | n/a |
| marketing_campaigns | dimension | 1 | 0.0 MiB | 200 | 200 | 100.0 % | single file | n/a |
| transactions | fact | 1,097 | 770.9 MiB | 4,425,008 | 5,000,000 | 88.5 % | 2023-06-17 → 2026-06-17 | 0 |
| call_center_interactions | fact | 1,097 | 133.3 MiB | 686,296 | 800,000 | 85.8 % | 2023-06-17 → 2026-06-17 | 0 |
| call_transcripts | fact | 1,097 | 130.9 MiB | 171,321 | 200,000 | 85.7 % | 2023-06-17 → 2026-06-17 | 0 |
| satisfaction_surveys | fact | 1,097 | 44.3 MiB | 212,759 | 250,000 | 85.1 % | 2023-06-17 → 2026-06-17 | 0 |
| digital_events | fact | 1,097 | 3,583.3 MiB | 15,620,994 | 10,000,000 | 156.2 % | 2023-06-17 → 2026-06-17 | 0 |
| complaints | fact | 1,097 | 17.2 MiB | 67,095 | 80,000 | 83.9 % | 2023-06-17 → 2026-06-17 | 0 |
| campaign_sends | fact | 1,083 | 310.9 MiB | 1,746,801 | 2,000,000 | 87.3 % | 2023-07-01 → 2026-06-17 | 0 |
| daily_exchange_rates | reference | 1 | 0.7 MiB | 13,164 | 3,000 | 438.8 % | single file | n/a |

## 4. Files and schema

| Table | Headers matching dictionary | Header variants | Files with byte-order mark | Undecodable headers | Files without a header row | Non-conforming paths | Extra columns | Missing columns |
|---|---|---|---|---|---|---|---|---|
| customers | 1 / 1 | 1 | 1 | 0 | 0 | 0 | none | none |
| products | 1 / 1 | 1 | 1 | 0 | 0 | 0 | none | none |
| branches | 1 / 1 | 1 | 1 | 0 | 0 | 0 | none | none |
| service_agents | 1 / 1 | 1 | 1 | 0 | 0 | 0 | none | none |
| marketing_campaigns | 1 / 1 | 1 | 1 | 0 | 0 | 0 | none | none |
| transactions | 1,097 / 1,097 | 1 | 1,097 | 0 | 0 | 0 | none | none |
| call_center_interactions | 1,097 / 1,097 | 1 | 1,097 | 0 | 0 | 0 | none | none |
| call_transcripts | 1,097 / 1,097 | 1 | 1,097 | 0 | 0 | 0 | none | none |
| satisfaction_surveys | 1,097 / 1,097 | 1 | 1,097 | 0 | 0 | 0 | none | none |
| digital_events | 1,097 / 1,097 | 1 | 1,097 | 0 | 0 | 0 | none | none |
| complaints | 1,097 / 1,097 | 1 | 1,097 | 0 | 0 | 0 | none | none |
| campaign_sends | 1,083 / 1,083 | 1 | 1,083 | 0 | 0 | 0 | none | none |
| daily_exchange_rates | 1 / 1 | 1 | 1 | 0 | 0 | 0 | none | none |

## 5. Keys and duplicates

Repeated keys are *identical* when the rows differ only by `process_date` (the same record delivered again) and *conflicting* when other columns differ.

| Table | Primary key | Rows | Distinct keys | Rows with a missing key | Extra rows | Duplicate rate | Identical re-deliveries | Conflicting versions |
|---|---|---|---|---|---|---|---|---|
| customers | customer_id | 150,000 | 150,000 | 0 | 0 | 0.00 % | 0 | 0 |
| products | product_id | 400,000 | 400,000 | 0 | 0 | 0.00 % | 0 | 0 |
| branches | branch_id | 350 | 350 | 0 | 0 | 0.00 % | 0 | 0 |
| service_agents | agent_id | 1,200 | 1,200 | 0 | 0 | 0.00 % | 0 | 0 |
| marketing_campaigns | campaign_id | 200 | 200 | 0 | 0 | 0.00 % | 0 | 0 |
| transactions | transaction_id | 4,425,008 | 4,425,008 | 0 | 0 | 0.00 % | 0 | 0 |
| call_center_interactions | interaction_id | 686,296 | 686,296 | 0 | 0 | 0.00 % | 0 | 0 |
| call_transcripts | transcript_id | 171,321 | 171,321 | 0 | 0 | 0.00 % | 0 | 0 |
| satisfaction_surveys | survey_id | 212,759 | 212,759 | 0 | 0 | 0.00 % | 0 | 0 |
| digital_events | event_id | 15,620,994 | 15,620,994 | 0 | 0 | 0.00 % | 0 | 0 |
| complaints | complaint_id | 67,095 | 67,095 | 0 | 0 | 0.00 % | 0 | 0 |
| campaign_sends | send_id | 1,746,801 | 1,746,801 | 0 | 0 | 0.00 % | 0 | 0 |
| daily_exchange_rates | date, source_currency, target_currency | 13,164 | 13,164 | 0 | 0 | 0.00 % | 0 | 0 |

## 6. Missing and malformed values

| Table | Nullable columns | Missing share of nullable cells |
|---|---|---|
| customers | 12 | 13.75 % |
| products | 6 | 39.60 % |
| branches | 6 | 0.00 % |
| service_agents | 5 | 19.28 % |
| marketing_campaigns | 7 | 21.14 % |
| transactions | 11 | 48.78 % |
| call_center_interactions | 10 | 16.37 % |
| call_transcripts | 9 | 8.00 % |
| satisfaction_surveys | 13 | 43.32 % |
| digital_events | 19 | 43.60 % |
| complaints | 16 | 60.95 % |
| campaign_sends | 13 | 63.44 % |
| daily_exchange_rates | 3 | 0.00 % |

Columns with contract-relevant problems:

| Column | Problem | Rows | Share of rows |
|---|---|---|---|
| customers.credit_score | integers written with a decimal point | 127,508 | 85.01 % |
| products.days_past_due | integers written with a decimal point | 125,350 | 31.34 % |
| service_agents.total_monthly_interactions | integers written with a decimal point | 1,089 | 90.75 % |
| call_center_interactions.duration_seconds | integers written with a decimal point | 590,062 | 85.98 % |
| call_center_interactions.wait_time_seconds | integers written with a decimal point | 480,678 | 70.04 % |
| call_transcripts.duration_seconds | missing values in a NOT NULL column | 24,029 | 14.03 % |
| call_transcripts.duration_seconds | integers written with a decimal point | 147,292 | 85.97 % |
| satisfaction_surveys.question_1_response | integers written with a decimal point | 121,370 | 57.05 % |
| satisfaction_surveys.question_2_response | integers written with a decimal point | 81,496 | 38.30 % |
| satisfaction_surveys.question_3_response | integers written with a decimal point | 40,103 | 18.85 % |
| digital_events.duration_seconds | integers written with a decimal point | 5,674,785 | 36.33 % |
| complaints.resolution_days | integers written with a decimal point | 15,363 | 22.90 % |
| complaints.resolution_satisfaction | integers written with a decimal point | 2,484 | 3.70 % |
| campaign_sends.click_count | integers written with a decimal point | 97,793 | 5.60 % |

## 7. Referential integrity

| Table | Reference | Non-null references | Orphans | Orphan rate |
|---|---|---|---|---|
| customers | registration_branch_id → branches.branch_id | 150,000 | 149,995 | 100.00 % |
| products | customer_id → customers.customer_id | 400,000 | 0 | 0.00 % |
| products | opening_branch_id → branches.branch_id | 400,000 | 0 | 0.00 % |
| service_agents | assigned_branch_id → branches.branch_id | 833 | 831 | 99.76 % |
| transactions | customer_id → customers.customer_id | 4,425,008 | 0 | 0.00 % |
| transactions | branch_id → branches.branch_id | 1,387,932 | 0 | 0.00 % |
| transactions | product_id → products.product_id | 4,425,008 | 0 | 0.00 % |
| call_center_interactions | customer_id → customers.customer_id | 686,296 | 0 | 0.00 % |
| call_center_interactions | agent_id → service_agents.agent_id | 686,296 | 0 | 0.00 % |
| call_transcripts | customer_id → customers.customer_id | 171,321 | 0 | 0.00 % |
| call_transcripts | agent_id → service_agents.agent_id | 171,321 | 0 | 0.00 % |
| call_transcripts | interaction_id → call_center_interactions.interaction_id | 171,321 | 0 | 0.00 % |
| satisfaction_surveys | customer_id → customers.customer_id | 212,759 | 0 | 0.00 % |
| satisfaction_surveys | agent_id → service_agents.agent_id | 212,759 | 0 | 0.00 % |
| satisfaction_surveys | interaction_id → call_center_interactions.interaction_id | 212,759 | 0 | 0.00 % |
| digital_events | customer_id → customers.customer_id | 11,875,548 | 0 | 0.00 % |
| digital_events | product_id → products.product_id | 1,440,338 | 0 | 0.00 % |
| complaints | customer_id → customers.customer_id | 67,095 | 0 | 0.00 % |
| complaints | assigned_agent_id → service_agents.agent_id | 43,980 | 0 | 0.00 % |
| complaints | affected_product_id → products.product_id | 44,570 | 0 | 0.00 % |
| complaints | related_branch_id → branches.branch_id | 19,178 | 0 | 0.00 % |
| complaints | origin_interaction_id → call_center_interactions.interaction_id | 0 | 0 | n/a |
| campaign_sends | customer_id → customers.customer_id | 1,746,801 | 0 | 0.00 % |
| campaign_sends | campaign_id → marketing_campaigns.campaign_id | 1,746,801 | 0 | 0.00 % |

## 8. Arrival lateness

Lag is the partition day minus the event day; positive values arrived after the day they describe. Events stamped after their own partition day that cluster in the first hours of the day point to partitions cut in a different time zone from the timestamps.

| Table | Rows measured | Partition ≠ process_date | Event after partition day | Hours of those events | Lag min (days) | p50 | p95 | p99 | Max | Over 7 days | Over 30 days |
|---|---|---|---|---|---|---|---|---|---|---|---|
| transactions | 4,425,008 | 0 | 1,106,307 | 0 to 6 | -1 | 0.00 | 0.00 | 0.00 | 0 | 0 | 0 |
| call_center_interactions | 686,296 | 0 | 228,318 | 0 to 8 | -1 | 0.00 | 0.00 | 0.00 | 0 | 0 | 0 |
| call_transcripts | 171,321 | 0 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | 0 | 0 |
| satisfaction_surveys | 212,759 | 0 | 169,092 | 0 to 23 | -2 | -1.00 | 0.00 | 0.00 | 0 | 0 | 0 |
| digital_events | 15,620,994 | 0 | 3,930,816 | 0 to 6 | -1 | 0.00 | 0.00 | 0.00 | 0 | 0 | 0 |
| complaints | 67,095 | 0 | 22,585 | 0 to 8 | -1 | 0.00 | 0.00 | 0.00 | 0 | 0 | 0 |
| campaign_sends | 1,746,801 | 0 | 436,429 | 0 to 6 | -1 | 0.00 | 0.00 | 0.00 | 0 | 0 | 0 |

## 9. Workload facts

### Fraud label

4,316 of 4,425,008 transactions are labelled as fraud (0.098 %).

| Month | Transactions | Fraud positives | Prevalence |
|---|---|---|---|
| 2023-06 | 56,260 | 54 | 0.096 % |
| 2023-07 | 121,313 | 138 | 0.114 % |
| 2023-08 | 127,592 | 137 | 0.107 % |
| 2023-09 | 119,559 | 97 | 0.081 % |
| 2023-10 | 127,793 | 140 | 0.110 % |
| 2023-11 | 121,984 | 125 | 0.102 % |
| 2023-12 | 125,969 | 123 | 0.098 % |
| 2024-01 | 122,201 | 121 | 0.099 % |
| 2024-02 | 117,135 | 118 | 0.101 % |
| 2024-03 | 121,588 | 135 | 0.111 % |
| 2024-04 | 121,330 | 125 | 0.103 % |
| 2024-05 | 126,371 | 132 | 0.104 % |
| 2024-06 | 121,816 | 110 | 0.090 % |
| 2024-07 | 128,772 | 115 | 0.089 % |
| 2024-08 | 122,764 | 129 | 0.105 % |
| 2024-09 | 117,525 | 131 | 0.111 % |
| 2024-10 | 127,493 | 137 | 0.107 % |
| 2024-11 | 121,481 | 122 | 0.100 % |
| 2024-12 | 122,761 | 107 | 0.087 % |
| 2025-01 | 122,180 | 122 | 0.100 % |
| 2025-02 | 110,465 | 110 | 0.100 % |
| 2025-03 | 123,716 | 112 | 0.091 % |
| 2025-04 | 120,744 | 103 | 0.085 % |
| 2025-05 | 126,997 | 131 | 0.103 % |
| 2025-06 | 118,788 | 140 | 0.118 % |
| 2025-07 | 126,882 | 130 | 0.102 % |
| 2025-08 | 126,039 | 119 | 0.094 % |
| 2025-09 | 118,563 | 107 | 0.090 % |
| 2025-10 | 129,268 | 125 | 0.097 % |
| 2025-11 | 120,775 | 103 | 0.085 % |
| 2025-12 | 122,382 | 115 | 0.094 % |
| 2026-01 | 121,962 | 109 | 0.089 % |
| 2026-02 | 115,098 | 87 | 0.076 % |
| 2026-03 | 123,915 | 111 | 0.090 % |
| 2026-04 | 126,492 | 117 | 0.092 % |
| 2026-05 | 127,559 | 109 | 0.085 % |
| 2026-06 | 71,476 | 70 | 0.098 % |

### USD amounts

`amount_usd` is present in 1,887,552 rows. Of those, 1,886,980 equal the local amount converted at the daily rate within tolerance, 0 do not, and 572 cannot be checked because no rate exists for their currency and day.

### Contact centre

686,296 interactions, of which 171,321 are flagged as having a transcript; the transcripts table holds 171,321 transcripts covering 171,321 distinct interactions.

Reason categories: `Transaccional` (240,056), `Producto` (150,863), `Queja` (117,021), `Técnico` (102,899), `Comercial` (54,879), `Retención` (20,578)

Contact reasons: `Transaccional` (240,056), `Producto` (150,863), `Queja` (117,021), `Técnico` (102,899), `Comercial` (54,879), `Retención` (20,578)

### Complaints

67,095 complaints; 15.03 % come from repeat complainers and 20.11 % breached their SLA.

Categories: `Transactions` (13,580), `Fees` (13,553), `Technical` (13,407), `Branch` (13,361), `Service` (13,194)

## Appendix. Column detail

### customers

| Column | Type | Nullable | Missing | Unparseable | Distinct (estimate) | Values |
|---|---|---|---|---|---|---|
| customer_id | VARCHAR(20) | no | 0.00 % | 0 | 173,849 |  |
| document_number | VARCHAR(20) | no | 0.00 % | 0 | 165,394 |  |
| document_type | VARCHAR(10) | no | 0.00 % | 0 | 4 | `DNI` (104,749), `CE` (15,150), `Pasaporte` (15,062), `CC` (15,039) |
| first_name | VARCHAR(100) | no | 0.00 % | 0 | 6,810 |  |
| last_name | VARCHAR(100) | no | 0.00 % | 0 | 4,365 |  |
| date_of_birth | DATE | no | 0.00 % | 0 | 26,753 |  |
| gender | VARCHAR(1) | yes | 0.00 % | 0 | 3 | `F` (50,508), `O` (49,808), `M` (49,684) |
| email | VARCHAR(100) | yes | 1.99 % | 0 | 84,951 |  |
| mobile_phone | VARCHAR(20) | yes | 3.14 % | 0 | 154,153 |  |
| landline_phone | VARCHAR(20) | yes | 50.04 % | 0 | 54,920 |  |
| address | VARCHAR(200) | yes | 4.91 % | 0 | 168,966 |  |
| city | VARCHAR(100) | no | 0.00 % | 0 | 16 | `Guadalajara` (12,643), `Ciudad de México` (12,506), `Querétaro` (12,500), `Tijuana` (12,489), `Puebla` (12,400), `Monterrey` (12,369), `Bogotá` (9,140), `Medellín` (9,107), … +8 more |
| state | VARCHAR(100) | no | 0.00 % | 0 | 18 | `Jalisco` (12,643), `Ciudad de México` (12,506), `Querétaro` (12,500), `Baja California` (12,489), `Puebla` (12,400), `Nuevo León` (12,369), `Cundinamarca` (9,140), `Antioquia` (9,107), … +8 more |
| country | VARCHAR(50) | no | 0.00 % | 0 | 3 | `México` (74,907), `Colombia` (45,251), `Argentina` (29,842) |
| postal_code | VARCHAR(10) | yes | 10.03 % | 0 | 7,594 |  |
| detected_accent | VARCHAR(50) | yes | 29.88 % | 0 | 3 | `mexican` (52,505), `<null>` (44,817), `colombian` (31,666), `argentine` (21,012) |
| segment | VARCHAR(50) | no | 0.00 % | 0 | 4 | `Basic` (89,756), `Plus` (37,547), `Premium` (15,207), `Student` (7,490) |
| credit_score | INTEGER | yes | 14.99 % | 0 | 442 |  |
| estimated_monthly_income | DECIMAL(12,2) | yes | 20.02 % | 0 | 92,660 |  |
| occupation | VARCHAR(100) | yes | 10.03 % | 0 | 21 | `<null>` (15,039), `Manager` (6,862), `Accountant` (6,857), `Salesperson` (6,823), `Homemaker` (6,806), `Entrepreneur` (6,805), `Doctor` (6,794), `Lawyer` (6,779), … +13 more |
| marital_status | VARCHAR(20) | yes | 7.97 % | 0 | 4 | `Married` (34,765), `Divorced` (34,734), `Single` (34,474), `Widowed` (34,072), `<null>` (11,955) |
| education_level | VARCHAR(50) | yes | 11.97 % | 0 | 5 | `College Prep` (39,583), `High School` (33,120), `University` (32,870), `<null>` (17,952), `Graduate` (13,269), `Elementary` (13,206) |
| registration_date | TIMESTAMP | no | 0.00 % | 0 | 152,920 |  |
| registration_branch_id | VARCHAR(20) | no | 0.00 % | 0 | 129,978 |  |
| customer_status | VARCHAR(20) | no | 0.00 % | 0 | 4 | `Active` (127,700), `Inactive` (14,914), `Suspended` (4,407), `Closed` (2,979) |
| last_updated | TIMESTAMP | no | 0.00 % | 0 | 140,354 |  |
| accepts_marketing | BOOLEAN | no | 0.00 % | 0 | 2 | `False` (75,007), `True` (74,993) |

### products

| Column | Type | Nullable | Missing | Unparseable | Distinct (estimate) | Values |
|---|---|---|---|---|---|---|
| product_id | VARCHAR(20) | no | 0.00 % | 0 | 467,436 |  |
| customer_id | VARCHAR(20) | no | 0.00 % | 0 | 172,609 |  |
| product_type | VARCHAR(50) | yes | 0.00 % | 0 | 7 | `Cuenta Ahorro` (120,203), `Tarjeta Crédito` (100,102), `Cuenta Corriente` (99,979), `Tarjeta Débito` (39,938), `Préstamo Personal` (19,960), `Préstamo Hipotecario` (11,910), `Inversión` (5,859), `Seguro` (2,049) |
| product_number | VARCHAR(30) | no | 0.00 % | 0 | 424,581 |  |
| currency | VARCHAR(3) | no | 0.00 % | 0 | 3 | `USD` (220,501), `COP` (107,975), `ARS` (71,524) |
| current_balance | DECIMAL(15,2) | no | 0.00 % | 0 | 341,358 |  |
| credit_limit | DECIMAL(15,2) | yes | 68.67 % | 0 | 143,753 |  |
| interest_rate | DECIMAL(5,2) | yes | 10.02 % | 0 | 4,057 |  |
| opening_date | DATE | no | 0.00 % | 0 | 3,237 |  |
| expiration_date | DATE | yes | 66.71 % | 0 | 3,689 |  |
| opening_branch_id | VARCHAR(20) | no | 0.00 % | 0 | 416 |  |
| product_status | VARCHAR(20) | no | 0.00 % | 0 | 4 | `Active` (339,965), `Closed` (32,039), `Blocked` (19,935), `Suspended` (8,061) |
| opening_channel | VARCHAR(30) | no | 0.00 % | 0 | 4 | `Branch` (199,838), `Web` (100,282), `App` (79,726), `Call Center` (20,154) |
| has_linked_app | BOOLEAN | no | 0.00 % | 0 | 2 | `False` (200,142), `True` (199,858) |
| days_past_due | INTEGER | yes | 68.66 % | 0 | 7 |  |
| last_transaction_date | TIMESTAMP | yes | 23.57 % | 0 | 307,663 |  |
| last_updated | TIMESTAMP | no | 0.00 % | 0 | 450,378 |  |

### branches

| Column | Type | Nullable | Missing | Unparseable | Distinct (estimate) | Values |
|---|---|---|---|---|---|---|
| branch_id | VARCHAR(20) | no | 0.00 % | 0 | 416 |  |
| branch_code | VARCHAR(10) | no | 0.00 % | 0 | 342 |  |
| branch_name | VARCHAR(100) | no | 0.00 % | 0 | 171 |  |
| branch_type | VARCHAR(30) | no | 0.00 % | 0 | 4 | `Express` (130), `Corporate` (129), `Premium` (48), `Main` (43) |
| address | VARCHAR(200) | no | 0.00 % | 0 | 318 |  |
| city | VARCHAR(100) | no | 0.00 % | 0 | 16 | `Puebla` (33), `Guadalajara` (30), `Ciudad de México` (29), `Tijuana` (29), `Monterrey` (28), `Querétaro` (26), `Barranquilla` (24), `Cartagena` (23), … +8 more |
| state | VARCHAR(100) | no | 0.00 % | 0 | 18 | `Puebla` (33), `Jalisco` (30), `Baja California` (29), `Ciudad de México` (29), `Nuevo León` (28), `Querétaro` (26), `Atlántico` (24), `Bolívar` (23), … +8 more |
| country | VARCHAR(50) | no | 0.00 % | 0 | 3 | `México` (175), `Colombia` (105), `Argentina` (70) |
| postal_code | VARCHAR(10) | yes | 0.00 % | 0 | 107 |  |
| geographic_zone | VARCHAR(50) | no | 0.00 % | 0 | 1 | `Urbana` (350) |
| phone | VARCHAR(20) | no | 0.00 % | 0 | 396 |  |
| email | VARCHAR(100) | yes | 0.00 % | 0 | 361 |  |
| opening_time | TIME | no | 0.00 % | 0 | 4 |  |
| closing_time | TIME | no | 0.00 % | 0 | 5 |  |
| has_atms | BOOLEAN | no | 0.00 % | 0 | 1 | `True` (350) |
| atm_count | INTEGER | yes | 0.00 % | 0 | 7 |  |
| has_teller_windows | BOOLEAN | no | 0.00 % | 0 | 1 | `True` (350) |
| teller_window_count | INTEGER | yes | 0.00 % | 0 | 10 |  |
| latitude | DECIMAL(10,7) | yes | 0.00 % | 0 | 450 |  |
| longitude | DECIMAL(10,7) | yes | 0.00 % | 0 | 294 |  |
| branch_opening_date | DATE | no | 0.00 % | 0 | 320 |  |
| branch_status | VARCHAR(20) | no | 0.00 % | 0 | 2 | `Active` (336), `Temporarily Closed` (14) |

### service_agents

| Column | Type | Nullable | Missing | Unparseable | Distinct (estimate) | Values |
|---|---|---|---|---|---|---|
| agent_id | VARCHAR(20) | no | 0.00 % | 0 | 1,094 |  |
| employee_code | VARCHAR(15) | no | 0.00 % | 0 | 1,492 |  |
| first_name | VARCHAR(100) | no | 0.00 % | 0 | 401 |  |
| last_name | VARCHAR(100) | no | 0.00 % | 0 | 1,107 |  |
| email | VARCHAR(100) | no | 0.00 % | 0 | 1,312 |  |
| phone | VARCHAR(20) | yes | 5.75 % | 0 | 1,341 |  |
| native_accent | VARCHAR(50) | no | 0.00 % | 0 | 3 | `mexican` (600), `colombian` (360), `argentine` (240) |
| country_of_origin | VARCHAR(50) | no | 0.00 % | 0 | 3 | `Mexico` (600), `Colombia` (360), `Argentina` (240) |
| assigned_branch_id | VARCHAR(20) | yes | 30.58 % | 0 | 765 |  |
| agent_type | VARCHAR(30) | no | 0.00 % | 0 | 4 | `Phone` (588), `Digital` (251), `In-Person` (230), `Hybrid` (131) |
| experience_level | VARCHAR(20) | no | 0.00 % | 0 | 4 | `Specialist` (761), `Senior` (286), `Mid-Senior` (135), `Junior` (18) |
| languages | VARCHAR(100) | no | 0.00 % | 0 | 4 | `español` (649), `español, inglés` (422), `español, portugués` (68), `español, inglés, portugués` (61) |
| specialty | VARCHAR(100) | yes | 39.67 % | 0 | 7 | `<null>` (476), `Fraudes` (105), `Cobranza` (97), `Retención` (96), `Soporte Técnico` (96), `Créditos` (87), `Inversiones` (83), `Ventas` (82), … +1 more |
| hire_date | DATE | no | 0.00 % | 0 | 991 |  |
| avg_csat | DECIMAL(3,2) | yes | 11.17 % | 0 | 142 |  |
| total_monthly_interactions | INTEGER | yes | 9.25 % | 0 | 473 |  |
| agent_status | VARCHAR(20) | no | 0.00 % | 0 | 4 | `Active` (1,090), `Vacation` (62), `Leave` (29), `Inactive` (19) |
| work_shift | VARCHAR(20) | no | 0.00 % | 0 | 3 | `Afternoon` (418), `Morning` (398), `Rotating` (203), `Night` (181) |

### marketing_campaigns

| Column | Type | Nullable | Missing | Unparseable | Distinct (estimate) | Values |
|---|---|---|---|---|---|---|
| campaign_id | VARCHAR(20) | no | 0.00 % | 0 | 207 |  |
| campaign_name | VARCHAR(150) | no | 0.00 % | 0 | 188 |  |
| description | TEXT | yes | 19.50 % | 0 | 35 |  |
| campaign_type | VARCHAR(50) | no | 0.00 % | 0 | 6 | `Email` (67), `SMS` (42), `WhatsApp` (37), `Push` (24), `Mix` (21), `Voice` (9) |
| campaign_objective | VARCHAR(100) | yes | 0.00 % | 0 | 5 | `Retention` (62), `Cross-sell` (51), `Acquisition` (38), `Reactivation` (25), `Up-sell` (24) |
| promoted_product | VARCHAR(50) | yes | 11.00 % | 0 | 6 | `Tarjeta Crédito` (52), `Cuenta Ahorro` (40), `Préstamo Personal` (28), `<null>` (22), `Inversión` (20), `Cuenta Corriente` (17), `Préstamo Hipotecario` (11), `Seguro` (10) |
| target_segment | VARCHAR(50) | yes | 39.50 % | 0 | 4 | `<null>` (79), `Plus` (32), `Premium` (32), `Basic` (30), `Student` (27) |
| target_country | VARCHAR(50) | yes | 55.50 % | 0 | 3 | `<null>` (111), `Colombia` (33), `Argentina` (28), `Mexico` (28) |
| start_date | DATE | no | 0.00 % | 0 | 214 |  |
| end_date | DATE | no | 0.00 % | 0 | 203 |  |
| budget | DECIMAL(12,2) | yes | 15.50 % | 0 | 179 |  |
| campaign_status | VARCHAR(20) | no | 0.00 % | 0 | 3 | `Completed` (172), `Paused` (25), `Active` (3) |
| expected_conversion_rate | DECIMAL(5,2) | yes | 7.00 % | 0 | 156 |  |

### transactions

| Column | Type | Nullable | Missing | Unparseable | Distinct (estimate) | Values |
|---|---|---|---|---|---|---|
| transaction_id | VARCHAR(30) | no | 0.00 % | 0 | 4,394,559 |  |
| transaction_date | TIMESTAMP | no | 0.00 % | 0 | 3,776,378 |  |
| process_date | DATE | no | 0.00 % | 0 | 1,215 |  |
| product_id | VARCHAR(20) | no | 0.00 % | 0 | 405,731 |  |
| customer_id | VARCHAR(20) | no | 0.00 % | 0 | 167,822 |  |
| transaction_type | VARCHAR(50) | yes | 0.00 % | 0 | 6 | `Purchase` (1,083,406), `Withdrawal` (964,673), `Transfer` (896,438), `Payment` (738,964), `Deposit` (609,409), `Adjustment` (132,118) |
| transaction_category | VARCHAR(50) | yes | 60.87 % | 0 | 6 | `<null>` (2,693,520), `Food` (432,468), `Services` (345,370), `Other` (260,518), `Transport` (260,316), `Entertainment` (259,643), `Health` (173,173) |
| amount | DECIMAL(15,2) | no | 0.00 % | 0 | 2,816,513 |  |
| currency | VARCHAR(3) | no | 0.00 % | 0 | 3 | `USD` (2,437,979), `COP` (1,194,444), `ARS` (792,585) |
| amount_usd | DECIMAL(15,2) | yes | 57.34 % | 0 | 656,182 |  |
| channel | VARCHAR(30) | no | 0.00 % | 0 | 6 | `POS` (1,548,161), `ATM` (1,328,334), `Web` (663,445), `App` (663,414), `Branch` (132,495), `Transfer` (89,159) |
| branch_id | VARCHAR(20) | yes | 68.63 % | 0 | 416 |  |
| merchant_name | VARCHAR(150) | yes | 76.74 % | 0 | 28 | `<null>` (3,395,774), `Super Ahorro` (64,527), `Restaurante El Buen Sabor` (64,370), `Tienda Don José` (64,249), `Mercado Central` (63,912), `Empresa Telefónica` (51,464), `Cable TV` (51,430), `Servicios Públicos` (51,250), … +17 more |
| merchant_category | VARCHAR(50) | yes | 76.75 % | 0 | 6 | `<null>` (3,396,215), `Food` (256,846), `Services` (205,124), `Other` (155,029), `Transport` (154,931), `Entertainment` (153,960), `Health` (102,903) |
| transaction_country | VARCHAR(50) | no | 0.00 % | 0 | 6 | `México` (2,105,794), `Colombia` (1,289,503), `Argentina` (867,561), `USA` (40,621), `Spain` (40,542), `Mexico` (40,515), `Brazil` (40,472) |
| transaction_city | VARCHAR(100) | yes | 10.00 % | 0 | 30 |  |
| transaction_status | VARCHAR(20) | no | 0.00 % | 0 | 4 | `Approved` (4,070,681), `Declined` (221,234), `Pending` (88,343), `Reversed` (44,750) |
| response_code | VARCHAR(10) | yes | 5.00 % | 0 | 5 | `00` (3,867,312), `<null>` (221,033), `14` (84,472), `51` (84,179), `05` (84,141), `54` (83,871) |
| is_fraud | BOOLEAN | no | 0.00 % | 0 | 2 | `False` (4,420,692), `True` (4,316) |
| fraud_score | DECIMAL(5,2) | yes | 20.00 % | 0 | 5,159 |  |
| latitude | DECIMAL(10,7) | yes | 80.63 % | 0 | 1,024,504 |  |
| longitude | DECIMAL(10,7) | yes | 80.63 % | 0 | 992,206 |  |

### call_center_interactions

| Column | Type | Nullable | Missing | Unparseable | Distinct (estimate) | Values |
|---|---|---|---|---|---|---|
| interaction_id | VARCHAR(30) | no | 0.00 % | 0 | 715,842 |  |
| interaction_date | TIMESTAMP | no | 0.00 % | 0 | 728,894 |  |
| process_date | DATE | no | 0.00 % | 0 | 1,215 |  |
| customer_id | VARCHAR(20) | no | 0.00 % | 0 | 164,963 |  |
| agent_id | VARCHAR(20) | yes | 0.00 % | 0 | 1,052 |  |
| interaction_type | VARCHAR(30) | no | 0.00 % | 0 | 5 | `Inbound Call` (480,678), `Outbound Call` (102,572), `Chat` (68,691), `Email` (27,543), `Video` (6,812) |
| channel | VARCHAR(30) | no | 0.00 % | 0 | 6 | `Phone` (583,250), `Email` (27,543), `App` (26,364), `WhatsApp` (22,888), `Web Chat` (22,856), `Web` (3,395) |
| contact_reason | VARCHAR(100) | no | 0.00 % | 0 | 6 | `Transaccional` (240,056), `Producto` (150,863), `Queja` (117,021), `Técnico` (102,899), `Comercial` (54,879), `Retención` (20,578) |
| reason_category | VARCHAR(50) | yes | 0.00 % | 0 | 6 | `Transaccional` (240,056), `Producto` (150,863), `Queja` (117,021), `Técnico` (102,899), `Comercial` (54,879), `Retención` (20,578) |
| duration_seconds | INTEGER | yes | 14.02 % | 0 | 986 |  |
| wait_time_seconds | INTEGER | yes | 29.96 % | 0 | 312 |  |
| was_resolved | BOOLEAN | yes | 0.00 % | 0 | 2 | `True` (526,030), `False` (160,266) |
| requires_followup | BOOLEAN | no | 0.00 % | 0 | 2 | `False` (447,242), `True` (239,054) |
| detected_sentiment | VARCHAR(20) | yes | 0.00 % | 0 | 5 | `Neutral` (459,712), `Negativo` (94,322), `Positivo` (75,562), `Muy Negativo` (37,727), `Muy Positivo` (18,973) |
| sentiment_score | DECIMAL(3,2) | yes | 0.00 % | 0 | 306 |  |
| customer_detected_accent | VARCHAR(50) | yes | 29.83 % | 0 | 3 | `mexican` (240,674), `<null>` (204,750), `colombian` (144,712), `argentine` (96,160) |
| agent_used_accent | VARCHAR(50) | yes | 29.83 % | 0 | 3 | `mexican` (241,931), `<null>` (204,750), `colombian` (144,386), `argentine` (95,229) |
| was_escalated | BOOLEAN | no | 0.00 % | 0 | 2 | `False` (617,910), `True` (68,386) |
| mentioned_products | VARCHAR(200) | yes | 60.03 % | 0 | 312,189 |  |
| has_transcript | BOOLEAN | no | 0.00 % | 0 | 2 | `False` (514,975), `True` (171,321) |
| has_recording | BOOLEAN | no | 0.00 % | 0 | 2 | `True` (590,062), `False` (96,234) |

### call_transcripts

| Column | Type | Nullable | Missing | Unparseable | Distinct (estimate) | Values |
|---|---|---|---|---|---|---|
| transcript_id | VARCHAR(30) | no | 0.00 % | 0 | 180,126 |  |
| interaction_id | VARCHAR(30) | no | 0.00 % | 0 | 208,322 |  |
| process_date | DATE | no | 0.00 % | 0 | 1,215 |  |
| customer_id | VARCHAR(20) | no | 0.00 % | 0 | 103,670 |  |
| agent_id | VARCHAR(20) | no | 0.00 % | 0 | 1,052 |  |
| full_text | TEXT | no | 0.00 % | 0 | 561 |  |
| customer_text | TEXT | yes | 0.00 % | 0 | 50 |  |
| agent_text | TEXT | yes | 0.00 % | 0 | 35 |  |
| detected_language | VARCHAR(10) | no | 0.00 % | 0 | 1 | `es` (171,321) |
| detected_accent | VARCHAR(50) | yes | 36.82 % | 0 | 3 | `<null>` (63,083), `mexican` (54,152), `colombian` (32,284), `argentine` (21,802) |
| accent_confidence | DECIMAL(3,2) | yes | 10.01 % | 0 | 27 |  |
| detected_keywords | VARCHAR(500) | yes | 5.13 % | 0 | 12 | `banco, servicio, cuenta` (18,173), `cuenta, servicio, banco` (18,116), `cuenta, banco, servicio` (18,113), `servicio, banco, cuenta` (18,016), `servicio, cuenta, banco` (18,016), `banco, cuenta, servicio` (17,910), `cuenta, banco` (9,082), `cuenta, servicio` (9,053), … +5 more |
| mentioned_entities | TEXT | yes | 10.02 % | 0 | 54 |  |
| detected_intents | VARCHAR(300) | yes | 4.94 % | 0 | 1 | `consulta_general` (162,864), `<null>` (8,457) |
| main_topics | VARCHAR(300) | yes | 0.00 % | 0 | 6 | `Transaccional` (59,786), `Producto` (37,658), `Queja` (29,198), `Técnico` (25,691), `Comercial` (13,808), `Retención` (5,180) |
| transcription_model | VARCHAR(50) | no | 0.00 % | 0 | 4 | `AWS Transcribe` (43,117), `Whisper v3` (42,803), `Google STT` (42,740), `Azure Speech` (42,661) |
| audio_quality | VARCHAR(20) | yes | 5.04 % | 0 | 3 | `High` (114,371), `Medium` (40,350), `<null>` (8,638), `Low` (7,962) |
| duration_seconds | INTEGER | no | 14.03 % | 0 | 970 |  |

### satisfaction_surveys

| Column | Type | Nullable | Missing | Unparseable | Distinct (estimate) | Values |
|---|---|---|---|---|---|---|
| survey_id | VARCHAR(30) | no | 0.00 % | 0 | 202,151 |  |
| survey_date | TIMESTAMP | no | 0.00 % | 0 | 207,875 |  |
| process_date | DATE | no | 0.00 % | 0 | 1,215 |  |
| interaction_id | VARCHAR(30) | yes | 0.00 % | 0 | 245,496 |  |
| customer_id | VARCHAR(20) | no | 0.00 % | 0 | 140,954 |  |
| agent_id | VARCHAR(20) | yes | 0.00 % | 0 | 1,052 |  |
| survey_type | VARCHAR(20) | no | 0.00 % | 0 | 3 | `CSAT` (127,856), `NPS` (63,668), `CES` (21,235) |
| send_channel | VARCHAR(30) | no | 0.00 % | 0 | 5 | `Email` (84,880), `SMS` (63,798), `App` (42,595), `IVR` (10,836), `Web` (10,650) |
| main_score | INTEGER | no | 0.00 % | 0 | 7 |  |
| nps_category | VARCHAR(20) | yes | 71.61 % | 0 | 2 | `<null>` (152,365), `Detractor` (45,007), `Passive` (15,387) |
| question_1_text | TEXT | yes | 42.99 % | 0 | 3 |  |
| question_1_response | INTEGER | yes | 42.95 % | 0 | 5 |  |
| question_2_text | TEXT | yes | 61.75 % | 0 | 1 |  |
| question_2_response | INTEGER | yes | 61.70 % | 0 | 5 |  |
| question_3_text | TEXT | yes | 81.13 % | 0 | 1 |  |
| question_3_response | INTEGER | yes | 81.15 % | 0 | 5 |  |
| open_comments | TEXT | yes | 52.44 % | 0 | 15 |  |
| comment_sentiment | VARCHAR(20) | yes | 52.41 % | 0 | 3 | `<null>` (111,502), `Negative` (67,529), `Neutral` (26,774), `Positive` (6,954) |
| response_time_hours | DECIMAL(8,2) | yes | 0.00 % | 0 | 3,407 |  |
| campaign_response_rate | DECIMAL(5,2) | yes | 15.06 % | 0 | 2,465 |  |

### digital_events

| Column | Type | Nullable | Missing | Unparseable | Distinct (estimate) | Values |
|---|---|---|---|---|---|---|
| event_id | VARCHAR(30) | no | 0.00 % | 0 | 14,709,111 |  |
| event_date | TIMESTAMP | no | 0.00 % | 0 | 10,572,811 |  |
| process_date | DATE | no | 0.00 % | 0 | 1,215 |  |
| customer_id | VARCHAR(20) | yes | 23.98 % | 0 | 173,849 |  |
| session_id | VARCHAR(50) | no | 0.00 % | 0 | 1,837,582 |  |
| event_type | VARCHAR(50) | yes | 0.00 % | 0 | 7 | `PageView` (5,972,564), `Click` (3,585,034), `Login` (2,434,770), `Logout` (2,433,612), `FormSubmit` (596,908), `Error` (358,723), `Purchase` (239,383) |
| event_category | VARCHAR(50) | no | 0.00 % | 0 | 4 | `Authentication` (4,868,382), `Navigation` (3,961,324), `Product` (3,786,314), `Transaction` (3,004,974) |
| channel | VARCHAR(30) | no | 0.00 % | 0 | 4 | `Android App` (5,476,164), `iOS App` (3,899,497), `Desktop Web` (3,128,851), `Mobile Web` (3,116,482) |
| platform | VARCHAR(30) | yes | 5.00 % | 0 | 5 | `Android` (6,685,963), `iOS` (5,182,421), `Windows` (991,807), `Linux` (991,774), `MacOS` (988,502), `<null>` (780,527) |
| browser | VARCHAR(50) | yes | 62.02 % | 0 | 4 | `<null>` (9,687,736), `Safari` (1,728,968), `Chrome` (1,725,978), `Samsung Internet` (990,164), `Firefox` (744,247), `Edge` (743,901) |
| app_version | VARCHAR(20) | yes | 42.98 % | 0 | 526 |  |
| page_url | VARCHAR(300) | yes | 5.00 % | 0 | 13 | `/login` (2,312,677), `/logout` (2,312,586), `/products/loans` (1,199,796), `/products/savings` (1,198,797), `/products/credit-card` (1,198,674), `/payments` (952,561), `/transfer` (951,673), `/transactions` (950,903), … +5 more |
| page_title | VARCHAR(200) | yes | 4.99 % | 0 | 12 | `Cerrar Sesión` (2,312,841), `Iniciar Sesión` (2,312,796), `Préstamos` (1,199,615), `Cuenta de Ahorro` (1,198,585), `Tarjeta de Crédito` (1,198,485), `Pagar Servicios` (952,401), `Transferir` (951,874), `Mis Movimientos` (950,599), … +5 more |
| action | VARCHAR(100) | yes | 10.00 % | 0 | 10 | `view_product` (3,407,643), `logout` (2,191,640), `login` (2,189,929), `<null>` (1,561,432), `initiate_payment` (902,225), `initiate_transfer` (901,824), `view_transactions` (901,039), `view_accounts` (892,802), … +3 more |
| element_id | VARCHAR(100) | yes | 15.00 % | 0 | 13 |  |
| product_id | VARCHAR(20) | yes | 90.78 % | 0 | 465,190 |  |
| event_value | DECIMAL(15,2) | yes | 94.91 % | 0 | 496,939 |  |
| duration_seconds | INTEGER | yes | 63.67 % | 0 | 254 |  |
| ip_address | VARCHAR(45) | yes | 5.00 % | 0 | 1,606,932 |  |
| ip_country | VARCHAR(50) | yes | 0.00 % | 0 | 4 | `México` (6,242,893), `Colombia` (4,806,882), `Argentina` (3,533,045), `Mexico` (1,038,174) |
| ip_city | VARCHAR(100) | yes | 27.98 % | 0 | 16 | `<null>` (4,370,476), `Guadalajara` (951,621), `Querétaro` (938,075), `Tijuana` (935,526), `Puebla` (934,034), `Ciudad de México` (930,004), `Monterrey` (928,988), `Bogotá` (687,593), … +9 more |
| is_mobile | BOOLEAN | no | 0.00 % | 0 | 2 | `True` (12,492,143), `False` (3,128,851) |
| referrer | VARCHAR(300) | yes | 93.29 % | 0 | 4 | `<null>` (14,573,469), `https://www.instagram.com` (262,214), `https://email.marketing.com` (262,107), `https://www.google.com` (261,610), `https://www.facebook.com` (261,594) |
| utm_source | VARCHAR(100) | yes | 94.63 % | 0 | 4 | `<null>` (14,781,949), `email` (210,149), `direct` (209,721), `google` (209,669), `facebook` (209,506) |
| utm_medium | VARCHAR(100) | yes | 94.63 % | 0 | 4 | `<null>` (14,781,860), `organic` (210,210), `email` (209,778), `social` (209,627), `cpc` (209,519) |
| utm_campaign | VARCHAR(100) | yes | 94.63 % | 0 | 3 | `<null>` (14,782,077), `retention` (280,033), `spring_promo` (279,962), `new_users` (278,922) |

### complaints

| Column | Type | Nullable | Missing | Unparseable | Distinct (estimate) | Values |
|---|---|---|---|---|---|---|
| complaint_id | VARCHAR(30) | no | 0.00 % | 0 | 66,008 |  |
| creation_date | TIMESTAMP | no | 0.00 % | 0 | 78,331 |  |
| process_date | DATE | no | 0.00 % | 0 | 1,215 |  |
| customer_id | VARCHAR(20) | no | 0.00 % | 0 | 59,121 |  |
| case_type | VARCHAR(30) | no | 0.00 % | 0 | 4 | `Complaint` (40,452), `Claim` (16,598), `Request` (6,761), `Suggestion` (3,284) |
| category | VARCHAR(100) | no | 0.00 % | 0 | 5 | `Transactions` (13,580), `Fees` (13,553), `Technical` (13,407), `Branch` (13,361), `Service` (13,194) |
| subcategory | VARCHAR(100) | yes | 9.98 % | 0 | 5 | `Cargo no reconocido` (12,297), `Cobro indebido` (12,194), `Problema con app` (12,128), `Atención en sucursal` (11,892), `Calidad de servicio` (11,886), `<null>` (6,698) |
| reception_channel | VARCHAR(30) | no | 0.00 % | 0 | 5 | `Call Center` (33,761), `Email` (13,323), `Web` (9,884), `App` (6,727), `Branch` (2,683), `Regulator` (717) |
| affected_product_id | VARCHAR(20) | yes | 33.57 % | 0 | 44,958 |  |
| related_branch_id | VARCHAR(20) | yes | 71.42 % | 0 | 416 |  |
| origin_interaction_id | VARCHAR(30) | yes | 100.00 % | 0 | 0 |  |
| description | TEXT | no | 0.00 % | 0 | 5 |  |
| claimed_amount | DECIMAL(15,2) | yes | 67.58 % | 0 | 22,492 |  |
| currency | VARCHAR(3) | yes | 67.54 % | 0 | 4 | `<null>` (45,319), `MXN` (5,487), `COP` (5,456), `USD` (5,431), `ARS` (5,402) |
| priority | VARCHAR(20) | no | 0.00 % | 0 | 4 | `Medium` (33,439), `Low` (20,411), `High` (9,890), `Critical` (3,355) |
| status | VARCHAR(30) | yes | 0.00 % | 0 | 6 | `In Process` (26,823), `Open` (20,125), `Resolved` (13,512), `Escalated` (3,321), `Closed` (2,609), `Rejected` (705) |
| assigned_agent_id | VARCHAR(20) | yes | 34.45 % | 0 | 1,094 |  |
| assignment_date | TIMESTAMP | yes | 34.47 % | 0 | 53,870 |  |
| first_response_date | TIMESTAMP | yes | 39.11 % | 0 | 35,422 |  |
| resolution_date | TIMESTAMP | yes | 77.12 % | 0 | 13,413 |  |
| closing_date | TIMESTAMP | yes | 96.30 % | 0 | 2,501 |  |
| sla_breached | BOOLEAN | no | 0.00 % | 0 | 2 | `False` (53,600), `True` (13,495) |
| resolution_days | INTEGER | yes | 77.10 % | 0 | 30 |  |
| resolution | TEXT | yes | 77.18 % | 0 | 4 |  |
| compensation_granted | DECIMAL(15,2) | yes | 93.08 % | 0 | 4,511 |  |
| resolution_satisfaction | INTEGER | yes | 96.30 % | 0 | 5 |  |
| is_repeat_complainer | BOOLEAN | no | 0.00 % | 0 | 2 | `False` (57,009), `True` (10,086) |

### campaign_sends

| Column | Type | Nullable | Missing | Unparseable | Distinct (estimate) | Values |
|---|---|---|---|---|---|---|
| send_id | VARCHAR(30) | no | 0.00 % | 0 | 2,203,531 |  |
| send_date | TIMESTAMP | no | 0.00 % | 0 | 1,427,643 |  |
| process_date | DATE | no | 0.00 % | 0 | 1,200 |  |
| campaign_id | VARCHAR(20) | no | 0.00 % | 0 | 164 |  |
| customer_id | VARCHAR(20) | no | 0.00 % | 0 | 173,849 |  |
| send_channel | VARCHAR(30) | no | 0.00 % | 0 | 5 | `Email` (620,195), `SMS` (432,283), `WhatsApp` (349,144), `Push` (290,650), `Voice` (54,529) |
| template_used | VARCHAR(100) | yes | 10.03 % | 0 | 858 |  |
| subject | VARCHAR(200) | yes | 68.03 % | 0 | 9 | `<null>` (1,188,342), `¡Oferta especial en Tarjeta Crédito!` (191,344), `¡Oferta especial en Préstamo Personal!` (95,544), `¡Oferta especial en Cuenta Corriente!` (79,322), `¡Oferta especial en Cuenta Ahorro!` (66,310), `¡Oferta especial en Inversión!` (38,402), `¡Oferta especial en nan!` (38,142), `¡Oferta especial en Seguro!` (25,469), … +1 more |
| send_status | VARCHAR(20) | no | 0.00 % | 0 | 4 | `Sent` (1,642,044), `Failed` (52,306), `Bounced` (34,900), `Blocked` (17,551) |
| was_delivered | BOOLEAN | no | 0.00 % | 0 | 2 | `True` (1,642,044), `False` (104,757) |
| was_opened | BOOLEAN | yes | 27.72 % | 0 | 2 | `False` (775,263), `True` (487,309), `<null>` (484,229) |
| open_date | TIMESTAMP | yes | 72.10 % | 0 | 535,875 |  |
| was_clicked | BOOLEAN | yes | 0.00 % | 0 | 2 | `False` (1,649,008), `True` (97,793) |
| click_date | TIMESTAMP | yes | 94.40 % | 0 | 135,250 |  |
| click_count | INTEGER | yes | 94.40 % | 0 | 5 |  |
| had_conversion | BOOLEAN | no | 0.00 % | 0 | 2 | `False` (1,737,002), `True` (9,799) |
| conversion_date | TIMESTAMP | yes | 99.44 % | 0 | 13,027 |  |
| conversion_value | DECIMAL(15,2) | yes | 99.44 % | 0 | 13,870 |  |
| open_device | VARCHAR(30) | yes | 74.90 % | 0 | 3 | `<null>` (1,308,424), `Desktop` (146,494), `Tablet` (146,450), `Mobile` (145,433) |
| open_country | VARCHAR(50) | yes | 74.90 % | 0 | 3 | `<null>` (1,308,278), `México` (219,090), `Colombia` (132,441), `Argentina` (86,992) |
| failure_reason | VARCHAR(200) | yes | 94.30 % | 0 | 3 | `<null>` (1,647,204), `SMTP error` (49,706), `Invalid email address` (33,209), `User blocked sender` (16,682) |
| send_cost | DECIMAL(10,4) | yes | 15.00 % | 0 | 2,305 |  |

### daily_exchange_rates

| Column | Type | Nullable | Missing | Unparseable | Distinct (estimate) | Values |
|---|---|---|---|---|---|---|
| date | DATE | no | 0.00 % | 0 | 1,215 |  |
| source_currency | VARCHAR(3) | no | 0.00 % | 0 | 4 |  |
| target_currency | VARCHAR(3) | no | 0.00 % | 0 | 4 |  |
| exchange_rate | DECIMAL(12,6) | no | 0.00 % | 0 | 7,717 |  |
| buy_rate | DECIMAL(12,6) | yes | 0.00 % | 0 | 9,411 |  |
| sell_rate | DECIMAL(12,6) | yes | 0.00 % | 0 | 9,657 |  |
| source | VARCHAR(50) | yes | 0.00 % | 0 | 4 | `Bloomberg` (3,303), `Reuters` (3,297), `Internal` (3,293), `Central Bank` (3,271) |
