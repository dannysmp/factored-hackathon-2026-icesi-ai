# Security Policy

Dispute Intake handles customer identity, transaction and case data. It is engineered to the
standards below, and every change is reviewed against them before it can merge.

## Reporting a vulnerability

Open a private security advisory on the repository (Security tab → *Report a vulnerability*).
Do not include credentials or personal data in a report. Reports are acknowledged within
72 hours. Remediation targets, measured from confirmation: **critical ≤ 48 hours, high ≤ 7 days,
medium ≤ 30 days**; exceptions are documented with the compensating controls.

## Security baseline

- **Standard:** OWASP ASVS Level 2, reviewed against the OWASP Top 10, API Security Top 10 and
  Top 10 for LLM Applications.
- **Data classification:** identity documents, account and card numbers, balances and
  transaction details are treated as restricted or confidential. Restricted values are never
  logged, stored beyond their purpose or sent to a model provider; confidential values are
  masked in output and redacted before leaving the service boundary.
- **Identity and authorization:** an identifier typed by a user never proves identity. Access is
  bound to an authenticated, short-lived session, and every data access and action is authorized
  in the service and tool layer, never in prompts.
- **Decisions outside the model:** eligibility, routing and confirmation rules are deterministic
  code with stable reason codes. Language models interpret and render text; they cannot decide
  outcomes or perform side effects on their own.
- **Side effects:** every state-changing operation requires explicit confirmation, an
  idempotency key and an audit record, and is read back before it is reported as done.

## Logging and audit

- **Operational logs** are structured, carry a trace and request identifier on every line, and
  use stable event names. They never contain secrets, restricted data, raw personal data, raw
  customer messages or full prompts; a redaction filter is a backstop and tests scan captured
  logs for leaks.
- **Audit records** are append-only, kept separately from operational logs, and record who did
  what, under which rule and policy version, with what outcome, so any decision can be
  reconstructed later.
- **Retention:** operational logs are short-lived; audit records and conversation transcripts
  follow a documented retention period with a purge procedure.

## Secrets and supply chain

- Secrets come only from the environment or a secret manager and are typed so they cannot be
  printed. Real `.env` files are git-ignored; the committed `.env.example` holds placeholders.
- CI carries no cloud credentials, uses a read-only token, pins third-party actions by commit
  SHA and scans every change for secrets, including full history.
- Dependencies are pinned by lockfile, audited on every change and updated through Dependabot.
  A new high or critical finding fails the build.
