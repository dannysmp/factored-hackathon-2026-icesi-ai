# Security policy

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
| Dependency lockfile, vulnerability audit in CI, dependency update automation | Implemented for the Python backend (`pip-audit`, blocking; Dependabot, monthly); the web frontend's dependencies have neither an automated update bot nor a CI-blocking audit today |
| CI workflow hardening: read-only token, actions pinned by commit SHA, no cloud credentials | Implemented |
| Review workflow: write scopes limited to commit statuses, labels and comments; third-party action pinned by commit SHA | Planned (scopes limited today; the action is pinned by release tag) |
| Session authentication: short-lived signed tokens, default-deny middleware, structured re-authentication errors, sandbox login limited per client and impossible in production | Implemented |
| Per-customer authorization in the tool layer | Implemented |
| One error format with stable codes and request identifiers; no stack traces or request data in responses | Implemented |
| Deterministic policy engine with stable reason codes | Implemented (`app/domain/policy/engine.py`: pure, fixed-order gates and routing over a stable `ReasonCode` set, wired into the controller as the only source of a filing decision) |
| Structured logging with correlation identifiers and redaction filter | Implemented (JSON logs with a stable event name, trace id and, once authenticated, session id on every line; card-shaped digit runs redacted before a line is written) |
| Append-only audit records | Implemented |
| Masking and redaction before model calls | Implemented (`app/llm/masking.py`: card-shaped digit runs redacted at the one egress boundary every outbound model request passes through; proven end to end by a request-capture fixture, `tests/test_request_capture_pii.py`). Document-number shapes (an unbroken run of seven or more digits, and the personal and company tax-number formats) are redacted the same way, by `redact_document_numbers`; a dotted national identity number has the shape of an amount and is not detected, a stated limitation (`docs/limitations.md`), not an unnoticed gap |
| Confirmation, idempotency and read-back for state-changing operations | Implemented (the create tool: explicit confirmation matched against the decision, an idempotency key enforced at the store, and an audit record for every filing and refusal; the controller's read-back call before reporting success sits outside the tool layer) |
| Retention and purge procedure | Planned |
| Demonstration sign-in broker: audience-separated signing keys, an access code compared before anything is counted, concurrent-session caps that release on a later refusal, and every attempt audited (append-only) before a token is returned | Implemented (customer and agent audiences, each with its own access code and signing key; the console's routes consume an agent session; its four writes (claim, release, note, status) are attributed to the acting agent and audited) |
| Security response headers and a post-deploy check on the live deployment | Implemented in part. `infra/Caddyfile` sets an enforcing Content-Security-Policy (with `frame-ancestors 'none'`), Strict-Transport-Security and removes the `Server` header; `infra/scripts/12-hardening-check.sh` checks that a TLS 1.1 handshake fails and that those headers are present after every deploy. Not covered: `Referrer-Policy`, `X-Frame-Options`, `X-Content-Type-Options` on the static pages the web tier serves, the dashboard host, and a probe of the negotiated cipher suites. See [the ASVS Level 1 checklist](docs/asvs-level1-checklist.md) (V9.1.2, V14.4.4 through V14.4.7) |
| Interactive API documentation and schema endpoints (`/docs`, `/redoc`, `/openapi.json`) disabled outside development | Planned. They stay at their framework defaults in the application; the deployed edge does not route those paths to it and the backend publishes no port |

This table is updated in the same change that implements a control. A full walk of the OWASP ASVS Level 1 baseline, every requirement mapped to its evidence or declared as a limitation, is at [`docs/asvs-level1-checklist.md`](docs/asvs-level1-checklist.md).
