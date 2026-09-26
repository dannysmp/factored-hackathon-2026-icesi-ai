# policy/

The versioned dispute policy and, in a later change, the multilingual policy corpus generated
from it.

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

## How a decision is made

The engine applies, in a fixed order, the eligibility gates (product, transaction type, status,
date, filing window, open case) and then the routing rules (fraud claim, low confidence, repeat
complainer, amount, risk score). Every decision carries a stable reason code, the facts it used and
the policy version. A fraud claim always goes to a person, and a risk score only routes: it never
decides an outcome.
