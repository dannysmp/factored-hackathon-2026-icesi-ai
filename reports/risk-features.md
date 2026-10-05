# Risk features

The table the transaction risk model learns from: one row per transaction, with features known when it happened (with the exceptions in section 3), the fraud label and the period of the row. Every figure is computed by `make features` from the cleaned layer.

## 1. Periods

4,425,008 transactions, 4,316 labelled fraud (0.098 % overall).

| Period | Days | Transactions | Fraud | Prevalence |
|---|---|---|---|---|
| train | up to and including 2025-03-31 | 2,628,068 | 2,640 | 0.100 % |
| validation | after 2025-03-31, through 2025-09-30 | 738,013 | 730 | 0.099 % |
| test | after 2025-09-30 | 1,058,927 | 946 | 0.089 % |

The boundaries are in `models/split.toml`; the model is evaluated on a period that follows the days it learned from.

## 2. Features and how much of each is present

The velocity, gap and distance features of a transaction use the same customer's strictly earlier transactions only; nothing recorded at the same instant or later is used.

| Feature | Meaning | Present |
|---|---|---|
| `amount_usd` | amount in US dollars, as stated or converted with the day's rate | 100.00 % |
| `amount_usd_source` | reported, converted or unknown | 100.00 % |
| `currency` | currency of the transaction | 100.00 % |
| `channel` | channel the transaction came through | 100.00 % |
| `transaction_type` | purchase, withdrawal, transfer, payment, deposit or adjustment | 100.00 % |
| `merchant_category` | category of the merchant, `unknown` when not stated | 100.00 % |
| `transaction_country` | country where the transaction happened | 100.00 % |
| `customer_country` | the customer's latest recorded country (see section 3) | 100.00 % |
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

## 3. Where a feature is not strictly point-in-time

- **The customer's country** (`customer_country`, and so `country_mismatch`) is the customer's latest recorded country, because the cleaned layer keeps one version of each customer. 32,124 of 150,000 customers were last updated after the training period ended, and 25.0 % of the transactions belong to a customer whose record is newer than the transaction. The source does not say what an update records, so these figures are an upper bound on how many countries could differ from the one at the time. A country that changed would be read as it is now, and a link from a fraud case to a later update of the record cannot be excluded from this data; model development compares results with and without these two features.
- **The exchange rate** used to convert an amount is the rate of the transaction's day; the source does not say at what time of the day it was published. The amounts the source states itself use the same day's rate.
- **Totals over a window** treat an amount that could not be converted as zero, while the count includes the transaction; a total can therefore be slightly low.

## 4. Left out on purpose

| Source column | Why |
|---|---|
| `fraud_score` | the source's own fraud score: it is not an input known beforehand but the product of the label or of a detector that already ran (see the evidence below) |
| `response_code` | the authorisation outcome, known only after the decision the model informs |
| `transaction_status` | the outcome (approved, declined, pending, reversed), known only after |
| `is_fraud` | the label; present in the mart only as the target, never as a feature |
| `customer_id, product_id` | identifiers: the model must generalise across customers |

Evidence for the fraud score: no transaction that is not fraud scores above 30.0, while 55.0 % of the fraud transactions do, so the score carries the label (or a detector that already ran) and is left out.

## 5. Do the remaining features stand in for the excluded columns?

Fraud prevalence for each value of a feature that could reflect the outcome of the transaction (whether the amount had to be converted, whether the merchant is known, whether coordinates exist) and of the categorical features, overall and per period. A value whose prevalence differs sharply from the overall one, on many positives, would be a warning; differences on a handful of positives are noise, and among many groups a few will differ by about two standard errors by chance. These tables compare each feature with the label; they cannot show a link to the excluded outcome columns themselves.

| amount_usd_source | Transactions | Fraud | Prevalence | Train | Validation | Test |
|---|---|---|---|---|---|---|
| `converted` | 99,442 | 94 | 0.095 % | 0.083 % | 0.109 % | 0.113 % |
| `reported` | 4,325,531 | 4,222 | 0.098 % | 0.101 % | 0.099 % | 0.089 % |
| `unknown` | 35 | 0 | 0.000 % | n/a | n/a | 0.000 % |

| merchant category stated | Transactions | Fraud | Prevalence | Train | Validation | Test |
|---|---|---|---|---|---|---|
| `no` | 3,396,215 | 3,287 | 0.097 % | 0.099 % | 0.099 % | 0.089 % |
| `yes` | 1,028,793 | 1,029 | 0.100 % | 0.105 % | 0.099 % | 0.089 % |

| currency | Transactions | Fraud | Prevalence | Train | Validation | Test |
|---|---|---|---|---|---|---|
| `ARS` | 792,585 | 787 | 0.099 % | 0.099 % | 0.106 % | 0.095 % |
| `COP` | 1,194,444 | 1,115 | 0.093 % | 0.098 % | 0.090 % | 0.084 % |
| `USD` | 2,437,979 | 2,414 | 0.099 % | 0.102 % | 0.101 % | 0.090 % |

| channel | Transactions | Fraud | Prevalence | Train | Validation | Test |
|---|---|---|---|---|---|---|
| `ATM` | 1,328,334 | 1,331 | 0.100 % | 0.102 % | 0.103 % | 0.094 % |
| `App` | 663,414 | 634 | 0.096 % | 0.104 % | 0.083 % | 0.084 % |
| `Branch` | 132,495 | 143 | 0.108 % | 0.121 % | 0.095 % | 0.085 % |
| `POS` | 1,548,161 | 1,467 | 0.095 % | 0.096 % | 0.098 % | 0.090 % |
| `Transfer` | 89,159 | 85 | 0.095 % | 0.091 % | 0.114 % | 0.094 % |
| `Web` | 663,445 | 656 | 0.099 % | 0.103 % | 0.107 % | 0.084 % |

| transaction_country | Transactions | Fraud | Prevalence | Train | Validation | Test |
|---|---|---|---|---|---|---|
| `Argentina` | 867,561 | 864 | 0.100 % | 0.101 % | 0.104 % | 0.094 % |
| `Brazil` | 40,472 | 45 | 0.111 % | 0.116 % | 0.120 % | 0.093 % |
| `Colombia` | 1,289,503 | 1,198 | 0.093 % | 0.096 % | 0.095 % | 0.084 % |
| `México` | 2,146,309 | 2,132 | 0.099 % | 0.103 % | 0.099 % | 0.091 % |
| `Spain` | 40,542 | 39 | 0.096 % | 0.116 % | 0.117 % | 0.031 % |
| `USA` | 40,621 | 38 | 0.094 % | 0.092 % | 0.073 % | 0.113 % |

| country_mismatch | Transactions | Fraud | Prevalence | Train | Validation | Test |
|---|---|---|---|---|---|---|
| `no` | 4,222,208 | 4,100 | 0.097 % | 0.099 % | 0.099 % | 0.090 % |
| `yes` | 202,800 | 216 | 0.107 % | 0.121 % | 0.095 % | 0.080 % |

| customer record newer than the transaction | Transactions | Fraud | Prevalence | Train | Validation | Test |
|---|---|---|---|---|---|---|
| `no` | 3,318,170 | 3,210 | 0.097 % | 0.101 % | 0.098 % | 0.089 % |
| `yes` | 1,106,838 | 1,106 | 0.100 % | 0.100 % | 0.105 % | 0.092 % |

| earlier transactions in the last 24 hours | Transactions | Fraud | Prevalence | Train | Validation | Test |
|---|---|---|---|---|---|---|
| `no` | 4,252,575 | 4,161 | 0.098 % | 0.101 % | 0.099 % | 0.089 % |
| `yes` | 172,433 | 155 | 0.090 % | 0.091 % | 0.091 % | 0.086 % |

| coordinates present | Transactions | Fraud | Prevalence | Train | Validation | Test |
|---|---|---|---|---|---|---|
| `no` | 3,610,593 | 3,541 | 0.098 % | 0.101 % | 0.099 % | 0.090 % |
| `yes` | 814,415 | 775 | 0.095 % | 0.097 % | 0.100 % | 0.086 % |

## 6. Lineage

| Cleaned table | SHA-256 |
|---|---|
| customers | 8fe5b47bc922 |
| daily_exchange_rates | c385e6d4f565 |
| transactions | 3c7933a5f82e |

Output digest `1dc73171d048`.
