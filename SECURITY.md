# Security Policy

This is a hackathon prototype built on synthetic data. It is not a production banking service.

## Reporting a vulnerability

Open a private security advisory on the repository (Security tab → *Report a vulnerability*).
Do not include real credentials or personal data in any report. We aim to acknowledge reports
within 72 hours.

## Scope and handling rules

- No real customer data exists in this repository; all data is synthetic or team-generated.
- Secrets never live in the repository: local `.env` files are git-ignored and CI carries no
  cloud credentials (see `.env.example` and the CI workflow).
- Dependencies are pinned by lockfile, audited in CI and updated through Dependabot.
