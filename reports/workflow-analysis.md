# Workflow analysis

Which part of the service demand is about disputed transactions, how it is handled today and what an automated workflow should achieve. Every figure is computed by `make analyze` from the marts listed in section 7; assumptions are named as such.

## 1. Scope, definitions and limits of the source

- **Dispute case:** a complaint of the category `Transactions` whose subcategory is `Cargo no reconocido` (an unrecognised charge). The source has no dispute flag, so this is a proxy. Undue charges (category `Fees`, subcategory `Cobro indebido`) are a neighbouring kind of dispute and are sized in section 2 as a sensitivity, together with the `Transactions` complaints that carry no subcategory. Duplicate charges cannot be told apart.
- **Contacts cannot be tied to cases:** the link from a complaint to the contact that originated it is empty in the source, and the contact reason repeats the six broad categories. Contact figures describe reason categories, not disputes; the cost model states how it bridges the gap.
- **Statuses:** only `Resolved` and `Closed` cases carry days to resolution; open, in-process, escalated and rejected cases do not.
- **Amounts** are reported per currency and never added across currencies; many cases carry no currency.
- **Marts read:** `dispute_cases_monthly`, `dispute_resolution`, `dispute_resolution_overall`, `dispute_claims_by_currency`, `complaint_category_mix`, `contact_demand_monthly`, `contact_satisfaction`.

## 2. Demand

Dispute cases: **12,297** between 2023-06 and 2026-06 (37 months with cases), 332 per month on average; the busiest month is 2024-04 with 388.

- Share of all complaints: **18.3 %** (12,297 of 67,095).
- Share of `Transactions` complaints: **90.6 %**.

Dispute cases per calendar year (partial years cover only the months in the data):

| Year | Cases |
|---|---|
| 2023 | 2,199 |
| 2024 | 4,053 |
| 2025 | 4,118 |
| 2026 | 1,927 |

Contacts by reason category (686,296 in total):

| Reason category | Contacts | Share |
|---|---|---|
| Transaccional | 240,056 | 35.0 % |
| Producto | 150,863 | 22.0 % |
| Queja | 117,021 | 17.1 % |
| Técnico | 102,899 | 15.0 % |
| Comercial | 54,879 | 8.0 % |
| Retención | 20,578 | 3.0 % |

Complaints by category:

| Category | Cases | Share |
|---|---|---|
| Transactions | 13,580 | 20.2 % |
| Fees | 13,553 | 20.2 % |
| Technical | 13,407 | 20.0 % |
| Branch | 13,361 | 19.9 % |
| Service | 13,194 | 19.7 % |

Complaints by category and subcategory, with the role each plays in the dispute definition:

| Category | Subcategory | Cases | Role |
|---|---|---|---|
| Branch | Atención en sucursal | 11,892 | not a dispute |
| Branch | unspecified | 1,469 | not a dispute |
| Fees | Cobro indebido | 12,194 | adjacent (sensitivity) |
| Fees | unspecified | 1,359 | not a dispute |
| Service | Calidad de servicio | 11,886 | not a dispute |
| Service | unspecified | 1,308 | not a dispute |
| Technical | Problema con app | 12,128 | not a dispute |
| Technical | unspecified | 1,279 | not a dispute |
| Transactions | Cargo no reconocido | 12,297 | primary dispute |
| Transactions | unspecified | 1,283 | unclassified (sensitivity) |

Sensitivity of the dispute count to the definition:

| Definition | Cases | Share of complaints |
|---|---|---|
| Primary: `Transactions` / `Cargo no reconocido` | 12,297 | 18.3 % |
| Plus undue charges (`Fees` / `Cobro indebido`) | 24,491 | 36.5 % |
| Plus `Transactions` without a subcategory | 25,774 | 38.4 % |

## 3. Resolution today

- Cases that reached `Resolved` or `Closed`: **24.5 %**; still open, in process or escalated: 74.6 %; rejected: 0.9 %.
- Days to resolution (resolved and closed cases): median **15.0**, 90th percentile 27.0.
- SLA breached: **20.4 %** of cases.
- Repeat complainers: **14.7 %** of cases.
- First response recorded: 61.5 % of cases.

| Status | Cases | Share | Mean days | Median days | P90 days | SLA breached |
|---|---|---|---|---|---|---|
| Closed | 498 | 4.0 % | 15.3 | 15.0 | 28.0 | 19.3 % |
| Escalated | 618 | 5.0 % | n/a | n/a | n/a | 23.0 % |
| In Process | 4,906 | 39.9 % | n/a | n/a | n/a | 21.1 % |
| Open | 3,648 | 29.7 % | n/a | n/a | n/a | 19.8 % |
| Rejected | 111 | 0.9 % | n/a | n/a | n/a | 16.2 % |
| Resolved | 2,516 | 20.5 % | 15.4 | 15.0 | 27.0 | 19.7 % |

Claimed amounts by currency (mean per case that states an amount):

| Currency | Cases | With amount | Mean claimed |
|---|---|---|---|
| ARS | 1,006 | 956 | 2,522.56 |
| COP | 1,012 | 977 | 2,445.57 |
| MXN | 1,034 | 989 | 2,589.83 |
| USD | 1,024 | 985 | 2,573.45 |
| unknown | 8,221 | 183 | 2,637.48 |

## 4. Handling, sentiment, satisfaction and outcome of contacts

Negative share is the share of contacts detected as `Negativo` or `Muy Negativo`; mean sentiment runs from -1 to 1; the survey score is the mean main score of the surveys tied to contacts of the category.

| Reason category | Mean handling (min) | Mean wait (min) | Negative | Neutral | Mean sentiment | Survey score | Resolved | Escalated | Follow-up needed |
|---|---|---|---|---|---|---|---|---|---|
| Comercial | 9.0 | 2.0 | 34.9 % | 40.1 % | -0.07 | 3.35 | 65.2 % | 9.8 % | 44.5 % |
| Producto | 4.4 | 2.0 | 19.2 % | 67.2 % | -0.04 | 3.74 | 89.6 % | 10.0 % | 23.8 % |
| Queja | 7.2 | 2.0 | 34.9 % | 40.2 % | -0.07 | 3.00 | 43.6 % | 10.0 % | 63.0 % |
| Retención | 8.0 | 2.0 | 34.9 % | 39.8 % | -0.07 | 3.26 | 60.2 % | 9.8 % | 49.1 % |
| Transaccional | 3.7 | 2.0 | 0.0 % | 100.0 % | 0.00 | 3.76 | 91.5 % | 9.9 % | 22.1 % |
| Técnico | 6.0 | 2.0 | 35.0 % | 39.9 % | -0.07 | 3.42 | 69.9 % | 10.1 % | 40.6 % |

`Transaccional` contacts are recorded as neutral in 100.0 % of cases, so their sentiment says little about how a customer feels about a dispute.
Surveys that reference no contact (0) are not attributed to a reason category.

## 5. Agent handling cost per dispute

The source records neither the cost of an agent nor the contacts a dispute needs, and contacts cannot be tied to cases, so the cost is a model, not a measurement:

`cost per dispute = contacts per dispute * handling time * agent-hour cost`

- Handling time: mean duration of `Transaccional` contacts, **3.7 minutes** (measured).
- Agent-hour cost: USD 6 / 9 / 14 (low / base / high; assumption).
- Contacts per dispute: 1 / 1.5 / 2.5 (low / base / high; assumption).

|  | Low | Base | High |
|---|---|---|---|
| Cost per dispute | USD 0.37 | USD 0.83 | USD 2.15 |
| Cost per month at 332 cases | USD 122.31 | USD 275.19 | USD 713.46 |

Which reason category models a dispute contact matters more than the low-to-high range: at the base assumptions, the cost per dispute by category is

| Reason category | Mean handling (min) | Base cost per dispute |
|---|---|---|
| Comercial | 9.0 | USD 2.02 |
| Producto | 4.4 | USD 1.00 |
| Queja | 7.2 | USD 1.63 |
| Retención | 8.0 | USD 1.80 |
| Transaccional | 3.7 | USD 0.83 |
| Técnico | 6.0 | USD 1.35 |

Waiting time is not costed: the customer waits, the agent does not. Back-office work on a case (investigation, contacting the merchant) is not recorded in the source and is not included, so agent time is understated by an unknown amount; the case for automation rests more on resolution time and SLA breaches (section 6) than on agent minutes. The assumptions live in `pipelines/analysis_assumptions.toml`.

## 6. Target outcomes for automation

The customer outcome is a dispute that is understood, confirmed and filed correctly in one conversation, or handed to a person with everything they need; the business outcome is fewer agent hours per dispute without unsafe actions. The targets below are team-defined proposals that the evaluation measures the system against; the baseline is what the data shows today.

| Outcome | Baseline today | Target |
|---|---|---|
| Safe automated resolution rate | not measured today | >= 40.0 % |
| SLA breach rate | 20.4 % | <= 5.0 % |
| Median days to resolution (resolved and closed cases only) | 15.0 | <= 3 |
| Repeat-complainer rate | 14.7 % | <= 10.0 % |
| Unsafe action rate | not measured today | <= 0.0 % |

The median is over the cases that reached `Resolved` or `Closed` (24.5 % of cases); the rest have no resolution time yet, so the baseline understates how long an open case waits.

## 7. Lineage

Every figure above is computed from the marts below, which are built from the cleaned tables of the data-quality report.

| Cleaned table | SHA-256 |
|---|---|
| call_center_interactions | 03b5a57f9fce |
| complaints | d2cc9a6f7bfd |
| satisfaction_surveys | 6e8d6b75fac2 |

| Mart | Rows | SHA-256 |
|---|---|---|
| `complaint_category_mix` | 10 | 595ffdb5ea90 |
| `contact_demand_monthly` | 222 | 072a6d9597d1 |
| `contact_satisfaction` | 6 | faa1541b03c3 |
| `dispute_cases_monthly` | 37 | 8b3145ad137d |
| `dispute_claims_by_currency` | 5 | b9c29b82c888 |
| `dispute_resolution` | 6 | 3dffc914f391 |
| `dispute_resolution_overall` | 1 | 3d6a17afd2a9 |
