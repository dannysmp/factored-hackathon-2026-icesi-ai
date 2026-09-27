---
lang: en
policy_version: "2"
generated: true
generated_from: "policy/dispute_policy_v1.yaml"
---

# Transaction dispute policy

## What this policy is {#overview}

This policy explains how a request to dispute a transaction made with an account or a card is decided. It is a synthetic policy written for this project: it is not any bank's or regulator's policy, and it is not legal advice. Every decision is made with fixed rules and recorded with a reason.

## Which transactions can be disputed {#who-can-dispute}

Transactions on these products can be disputed: Savings account, Checking account, Credit card and Debit card.

The other products (Personal loan, Mortgage, Investments and Insurance) have their own claim processes and are not handled by this policy.

To file a dispute, the transaction must be a charge to the customer (a purchase, a withdrawal, a transfer or a payment), be approved, not be dated in the future, be within the deadline of its category (see below) and have no other open dispute.

Transactions that are deposits or adjustments cannot be disputed.

Transactions that are declined, pending or reversed cannot be disputed either.

## Deadlines to file a dispute {#filing-windows}

A dispute must be filed within a deadline, counted in calendar days from the transaction date. The last day of the deadline is still valid: for example, with a deadline of 60 days, the dispute can be filed on day 60 but not on day 61.

- Unrecognized charge: 120 days.
- Duplicate charge: 60 days.
- Wrong amount: 90 days.
- Service not received: 120 days.
- Fraud claim: 180 days.

## When the first response arrives {#response-time}

After a dispute is filed, the bank gives a first response within this deadline, counted in calendar days from the filing date:

- Unrecognized charge: 3 days.
- Duplicate charge: 3 days.
- Wrong amount: 3 days.
- Service not received: 5 days.
- Fraud claim: 1 day.

## What to have ready {#evidence}

For each type of dispute, have the following ready:

- Unrecognized charge: confirm you still have your card and say which part of the charge you do not recognize (merchant, date or amount).
- Duplicate charge: the dates and amounts of both charges.
- Wrong amount: proof of the agreed amount, such as a receipt or an order confirmation.
- Service not received: proof of the order or payment and any attempt to contact the merchant.
- Fraud claim: whether the card is lost, stolen or still in your hands and when you last used it yourself.

## Confirmation before filing {#confirmation}

Before a dispute is filed, the customer confirms exactly what is going to be filed: the transaction, the reason and the details of the request.

## When a person reviews it {#human-review}

Even when a request meets the rules, a person reviews it in these cases:

- It is a fraud claim.
- The request was not understood with enough confidence.
- Other bank review criteria apply.

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
| `escalate_fraud_claim` | A person reviews the request. |
| `escalate_low_nlu_confidence` | A person reviews the request. |
| `escalate_repeat_complainer` | A person reviews the request. |
| `escalate_amount_above_threshold` | A person reviews the request. |
| `escalate_amount_unknown` | A person reviews the request. |
| `escalate_risk_score` | A person reviews the request. |
