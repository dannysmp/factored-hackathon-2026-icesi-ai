# Security Policy

Dispute Intake is designed to handle customer identity, transaction and case data. This document
states the requirements every change is held to and, separately, which controls exist in the
code today.

## Reporting a vulnerability

Open a private security advisory on the repository (Security tab → *Report a vulnerability*).
Do not include credentials or personal data in a report. Reports are acknowledged within
72 hours. Remediation targets, measured from confirmation: **critical ≤ 48 hours, high ≤ 7 days,
medium ≤ 30 days**; exceptions are documented with the compensating controls.

## Security requirements

- **Standard:** OWASP ASVS Level 2, reviewed against the OWASP Top 10, API Security Top 10 and
  Top 10 for LLM Applications.
- **Data classification:** identity documents, account and card numbers, balances and
  transaction details are restricted or confidential. Restricted values must never be logged,
  stored beyond their purpose or sent to a model provider; confidential values must be masked in
  output and redacted before leaving the service boundary.
- **Identity and authorization:** an identifier typed by a user must never prove identity.
  Access must be bound to an authenticated, short-lived session, and every data access and action
  must be authorized in the service and tool layer, never in prompts.
- **Decisions outside the model:** eligibility, routing and confirmation rules must be
  deterministic code with stable reason codes. Language models interpret and render text; they
  must not decide outcomes or perform side effects on their own.
- **Side effects:** every state-changing operation must require explicit confirmation, an
  idempotency key and an audit record, and must be read back before it is reported as done.
- **Operational logs:** structured, with a trace and request identifier on every line and stable
  event names. They must never contain secrets, restricted data, raw personal data, raw customer
  messages or full prompts; a redaction filter is a backstop and tests scan captured logs.
- **Audit records:** append-only, kept separately from operational logs, recording who did what,
  under which rule and policy version, with what outcome.
- **Retention:** operational logs are short-lived; audit records and conversation transcripts
  follow a documented retention period with a purge procedure.
- **Secrets and supply chain:** secrets come only from the environment or a secret manager and
  are typed so they cannot be printed; CI carries no cloud credentials and pins third-party
  actions by commit SHA; dependencies are pinned by lockfile and audited on every change, and a
  new high or critical finding fails the build.

## Implementation status

| Control | Status |
|---|---|
| Typed secrets, fail-fast validated configuration, model allow-list | Implemented |
| Secret scanning of history and staged changes, with a planted-key self-test | Implemented |
| Dependency lockfile, vulnerability audit in CI, dependency update automation | Implemented |
| CI workflow hardening: read-only token, actions pinned by commit SHA, no cloud credentials | Implemented |
| Review workflow: write scopes limited to commit statuses, labels and comments; third-party action pinned by commit SHA | Planned (scopes limited today; the action is pinned by release tag) |
| Session authentication and per-customer authorization in the tool layer | Planned |
| Deterministic policy engine with stable reason codes | Planned |
| Structured logging with correlation identifiers and redaction filter | Planned |
| Append-only audit records | Planned |
| Masking and redaction before model calls | Planned |
| Confirmation, idempotency and read-back for state-changing operations | Planned |
| Retention and purge procedure | Planned |

This table is updated in the same change that implements a control.
