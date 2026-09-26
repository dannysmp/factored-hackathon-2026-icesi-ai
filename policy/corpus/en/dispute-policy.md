---
lang: en
policy_version: "1"
generated: true
generated_from: "policy/dispute_policy_v1.yaml"
---

# Transaction dispute policy

## What this policy is {#overview}

This policy explains how a request to dispute a transaction made with an account or a card is decided. It is a synthetic policy written for this project: it is not any bank's or regulator's policy, and it is not legal advice. Every decision is made with fixed rules and recorded with a reason.

## Which transactions can be disputed {#who-can-dispute}

Transactions on these products can be disputed: Savings account, Checking account, Credit card and Debit card.

The other products (Personal loan, Mortgage, Investments and Insurance) have their own claim processes and are not handled by this policy.

To file a dispute, the transaction must be a charge to the customer (a purchase, a withdrawal, a transfer or a payment), be approved, be within the deadline of its category (see below) and have no other open dispute.

Transactions that are deposits or adjustments cannot be disputed.

Transactions that are declined, pending or reversed cannot be disputed either.

## Deadlines to file a dispute {#filing-windows}

A dispute must be filed within a deadline, counted in calendar days from the transaction date. The last day of the deadline is still valid: for example, with a deadline of 60 days, the dispute can be filed on day 60 but not on day 61.

- Unrecognized charge: 120 days.
- Duplicate charge: 60 days.
- Wrong amount: 90 days.
- Service not received: 120 days.
- Fraud claim: 180 days.

## Confirmation before filing {#confirmation}

Before a dispute is filed, the customer confirms exactly what is going to be filed: the transaction, the reason and the details of the request.

## When a person reviews it {#human-review}

Even when a request meets the rules, a person reviews it in these cases:

- It is a fraud claim.
- The request was not understood with enough confidence (below 60%).
- The customer has filed repeated complaints.
- The amount is 5,000 USD or more.
- The amount in US dollars is not known.
- The transaction's risk score is 0.80 or higher. The score only decides that a person reviews the dispute; it never decides the outcome.

## Fraud claims {#fraud-claims}

A fraud claim is always reviewed by a person. It is never refused automatically, even when the transaction was declined, is outside the deadline or is on a product outside the scope: in that case the person receives the reason the rule would have failed.

## Reasons for each decision {#decision-codes}

Every decision carries one of these reasons.

| Reason | Meaning |
|---|---|
| `eligible` | The dispute can be filed, after confirmation. |
| `product_out_of_scope` | The product is outside the scope of this policy. |
| `transaction_type_not_disputable` | The transaction type is not a charge that can be disputed. |
| `transaction_declined` | The transaction was declined: there was no charge. |
| `transaction_pending` | The transaction is still pending. |
| `transaction_reversed` | The transaction has already been reversed. |
| `transaction_date_in_future` | The transaction date is in the future. |
| `filing_window_expired` | The deadline to file this dispute has passed. |
| `duplicate_open_case` | A dispute is already open for this transaction. |
| `escalate_fraud_claim` | It is a fraud claim; a person reviews it. |
| `escalate_low_nlu_confidence` | The request was not understood with enough confidence; a person reviews it. |
| `escalate_repeat_complainer` | The customer has repeated complaints; a person reviews it. |
| `escalate_amount_above_threshold` | The amount reaches the review threshold; a person reviews it. |
| `escalate_amount_unknown` | The amount in US dollars is not known; a person reviews it. |
| `escalate_risk_score` | The risk score reaches the threshold; a person reviews it. |
