# policy/

The versioned dispute policy and the multilingual policy corpus generated from it.

## `dispute_policy_v1.yaml`

The parameters the policy engine (`app/domain/policy`) applies. They are data; the rules that use
them are code, so changing a window or a threshold never needs a code change, and a change to
the rules never hides in a parameter. A parameter change is a new policy version.

| Section | What it sets |
|---|---|
| `in_scope_product_types` | Products whose transactions can be disputed here (accounts and cards) |
| `disputable_transaction_types` | Transaction types that are a charge to the customer (purchase, withdrawal, transfer, payment) |
| `categories` | Per dispute category: the filing window in days (the last valid day equals the window) and whether the customer must confirm the exact filing |
| `routing` | When an otherwise eligible request goes to a person: amount from which a person reviews, minimum confidence in the understood request, risk-score threshold, repeat complainers and unknown amounts |

The values are synthetic planning values written for this project, inspired by common chargeback
and complaint-handling practice. They are not those of any bank or regulator, and the file says so.

## `corpus/`

The policy explained to customers in Spanish, Portuguese and English (`es/`, `pt/`, `en/`, one
`dispute-policy.md` each). The documents are **generated** from the YAML by
`app/domain/policy/corpus.py`; every number in them (windows, amount, confidence floor, risk
threshold) is read from the policy, never typed. The sections carry the same stable identifiers
in every language (`overview`, `who-can-dispute`, `filing-windows`, `confirmation`,
`human-review`, `fraud-claims`, `decision-codes`) so that an answer can cite one whichever
language the customer used, and rules a policy version switches off are left out of the text.

- `make corpus` regenerates the files; do not edit them by hand.
- `make corpus-check` and the test suite fail when the files differ from what the policy
  generates, so a parameter change that forgets to regenerate the corpus cannot be merged.

## How a decision is made

The engine applies, in a fixed order, the eligibility gates (product, transaction type, status,
date, filing window, open case) and then the routing rules (fraud claim, low confidence, repeat
complainer, amount, risk score). Every decision carries a stable reason code, the facts it used and
the policy version.

Two decisions are worth knowing:

- **A fraud claim always goes to a person.** It is never refused by a rule, even for a declined
  transaction, an expired window, a product out of scope or an open case: a customer reporting
  fraud gets a human. The gate that would have failed is recorded as the fact `eligibility_gate`
  so the person sees it (`tests/test_policy_engine.py`, the fraud tests and the property
  `test_a_fraud_claim_is_never_refused`).
- **Any other request that cannot be filed is refused before routing.** Routing to a person is for
  what could be filed; a declined transaction, for example, is refused whatever the amount or the
  risk score.

A risk score only routes: it never decides an outcome.

Product and transaction types are compared exactly as spelled; the service layer supplies the
canonical spellings of the cleaned data.
