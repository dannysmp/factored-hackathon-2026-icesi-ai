# Operational seed

**This seed is curated, not a random sample.** It is built by a written, deterministic rule (`pipelines.ops_seed`) so that every situation the policy distinguishes is present, even though most of those situations are rare in the source. A rate measured on this seed describes the seed, never the population; every rate below is shown beside the same rate measured on the full cleaned layer.

Reference date `2026-06-18` (the newest transaction instant in the seed, read by the running service as `ops_meta.data_as_of`, ADR-15).

500 customers selected, all `Active` (AC-E4-48). 1,368 products, 15,230 transactions.

## 1. Coverage of the selection rule (AC-E4-44)

How many selected customers carry each stratum; the rule guarantees at least 5 wherever the source has that many.

| Stratum | Selected customers |
|---|---|
| `is_repeat_complainer` | 28 |
| `has_open_case` | 138 |
| `has_unconvertible_amount` | 5 |
| `has_near_5000_transfer` | 108 |
| `has_declined` | 338 |
| `has_merchant_null` | 460 |
| `has_category_null` | 460 |
| `has_tx_60d_before` | 64 |
| `has_tx_90d_before` | 55 |
| `has_tx_120d_before` | 71 |

| Segment | Country | Selected customers |
|---|---|---|
| Basic | México | 142 |
| Basic | Colombia | 99 |
| Basic | Argentina | 51 |
| Plus | México | 56 |
| Plus | Colombia | 35 |
| Plus | Argentina | 35 |
| Premium | México | 27 |
| Premium | Colombia | 19 |
| Premium | Argentina | 9 |
| Student | México | 15 |
| Student | Colombia | 5 |
| Student | Argentina | 7 |

## 2. Null and mix rates: seed beside source

| Rate | Seed | Source |
|---|---|---|
| Merchant name null | 75.18 % | 76.74 % |
| Declined | 4.85 % | 5.00 % |
| Amount unknown | 0.03 % | 0.00 % |
| Amount converted | 2.18 % | 2.25 % |

| Segment | Seed | Source |
|---|---|---|
| Basic | 58.40 % | 59.84 % |
| Plus | 25.20 % | 25.03 % |
| Premium | 11.00 % | 10.14 % |
| Student | 5.40 % | 4.99 % |

| Country | Seed | Source |
|---|---|---|
| México | 48.00 % | 49.94 % |
| Colombia | 31.60 % | 30.17 % |
| Argentina | 20.40 % | 19.89 % |

## 3. What the seed does not contain

No document number, birth date, address, full email or full phone (AC-E4-46): the seed never reads those source columns, and the two contact fields it keeps are masked before they reach a Parquet file. `cases` starts empty; see the module's Limitations.

## 4. Lineage

| Cleaned table | SHA-256 |
|---|---|
| complaints | d2cc9a6f7bfd |
| customers | 8fe5b47bc922 |
| daily_exchange_rates | c385e6d4f565 |
| products | ba69f7bc2f26 |
| transactions | 3c7933a5f82e |

| Output | Rows | SHA-256 |
|---|---|---|
| customers.parquet | 500 | 4c2366e4076c |
| products.parquet | 1,368 | 3914855448e5 |
| transactions.parquet | 15,230 | f61b7200b88f |

Built by `3bc8308-dirty-d2383978`.
