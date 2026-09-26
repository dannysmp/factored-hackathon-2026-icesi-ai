<!--
Title: <type>(<scope>): <Imperative summary in sentence case>
       e.g. feat(policy): Add deterministic access request evaluation
Open as a draft. Fill every section; write "None." rather than deleting one.
-->

This pull request <states in one or two sentences what it delivers and why it matters, in the present tense: "This pull request adds …", "This pull request refactors …">.

### Summary of Changes

- **<Area, e.g. Domain Logic>**:
  - `<component or file>`: <what changed and why, in one or two sentences>.
  - `<component or file>`: <…>.
- **<Area, e.g. API>**:
  - `<endpoint or module>`: <…>.
- **<Area, e.g. Tests>**:
  - `<test file>`: <which behaviors it protects, including failure paths>.
- **<Area, e.g. Documentation>**:
  - `<doc>`: <what now reflects the new behavior>.

### Motivation and Context

<The problem or requirement this solves. Link the issue, ADR or plan step. Note any decision a reviewer should know about.>

### Claims

<Specific, checkable statements. The staff reviewer verifies each one.>

1. <e.g. "Requests older than 30 days are denied with reason REQUEST_EXPIRED; day 30 is still valid.">
2. <…>

### Verification

| Check | Command | Result |
|---|---|---|
| Format / lint / type-check | `<command>` | <result> |
| Tests | `<command>` | <N passed, M failed> |
| Coverage (diff / overall) | `<command>` | <x% / y%> |
| Security scans | `<command>` | <findings> |
| Manual check | <what you ran and observed> | <result> |

### Security, Privacy and Observability

- **Data handled:** <data classes touched, or "none">.
- **Controls:** <validation, authorization, redaction, secrets>.
- **Telemetry:** <new log events, metrics, audit records>.

### Risks and Rollback

- **Risks:** <what could go wrong in production and how it would show>.
- **Rollback:** <how to revert safely; migrations or data changes that need care>.

### Known Gaps and Follow-ups

<What was not done or not verified, and why. Tracked follow-ups.>
