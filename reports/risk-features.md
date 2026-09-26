# Risk features

The table the transaction risk model learns from: one row per transaction, with features known when it happened, the fraud label and the period the row belongs to. Every figure is computed by `make features` from the cleaned layer.

## 1. Periods

4,425,008 transactions, 4,316 labelled fraud (0.098 % overall).

| Period | Days | Transactions | Fraud | Prevalence |
|---|---|---|---|---|
| train | up to and including 2025-03-31 | 2,628,068 | 2,640 | 0.100 % |
| validation | after 2025-03-31, up to and including 2025-09-30 | 738,013 | 730 | 0.099 % |
| test | after 2025-09-30 | 1,058,927 | 946 | 0.089 % |

The boundaries are in `models/split.toml`; the model is evaluated on a period that follows the days it learned from.

## 2. Features and how much of each is present

Each feature of a transaction uses that transaction and the customer's earlier transactions only; the windows end strictly before it.

| Feature | Meaning | Present |
|---|---|---|
| `amount_usd` | amount in US dollars, as stated or converted with the day's rate | 100.00 % |
| `amount_usd_source` | reported, converted or unavailable | 100.00 % |
| `currency` | currency of the transaction | 100.00 % |
| `channel` | channel the transaction came through | 100.00 % |
| `transaction_type` | purchase, withdrawal, transfer, payment, deposit or adjustment | 100.00 % |
| `merchant_category` | category of the merchant, `unknown` when not stated | 100.00 % |
| `transaction_country` | country where the transaction happened | 100.00 % |
| `customer_country` | country of the customer's address | 100.00 % |
| `country_mismatch` | the two countries differ (empty when either is unknown) | 100.00 % |
| `hour` | hour of the day of the transaction | 100.00 % |
| `day_of_week` | day of the week, 1 (Monday) to 7 (Sunday) | 100.00 % |
| `is_weekend` | Saturday or Sunday | 100.00 % |
| `tx_count_24h` | the customer's transactions in the 24 hours before | 100.00 % |
| `tx_sum_usd_24h` | their total in US dollars in the 24 hours before | 100.00 % |
| `tx_count_7d` | the customer's transactions in the 7 days before | 100.00 % |
| `tx_sum_usd_7d` | their total in US dollars in the 7 days before | 100.00 % |
| `seconds_since_previous` | seconds since the customer's previous transaction (empty for the first) | 96.96 % |
| `distance_previous_km` | kilometres from where the customer's previous transaction with coordinates took place (empty when this one has no coordinates or there is no earlier one) | 15.45 % |

## 3. Left out on purpose

| Source column | Why |
|---|---|
| `fraud_score` | the source's own fraud score: it is not an input known beforehand but the product of the label or of a detector that already ran (see the evidence below) |
| `response_code` | the authorisation outcome, known only after the decision the model informs |
| `transaction_status` | the outcome (approved, declined, pending, reversed), known only after |
| `is_fraud` | the label; present in the mart only as the target, never as a feature |
| `customer_id, product_id` | identifiers: the model must generalise across customers |

Evidence for the fraud score: no transaction that is not fraud scores above 30.0, while 55.0 % of the fraud transactions do, so the score carries the label and is left out.

## 4. Lineage

| Cleaned table | SHA-256 |
|---|---|
| customers | 8fe5b47bc922 |
| daily_exchange_rates | c385e6d4f565 |
| transactions | 3c7933a5f82e |

Output digest `0725a5448dd1`.
