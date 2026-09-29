# infra/

AWS provisioning for the deployed stack (ADR-13: one EC2 host, ECR, docker compose, GitHub OIDC).

## What these scripts provision

| Script | What it creates |
|---|---|
| `scripts/01-create-oidc-role.sh` | The GitHub Actions OIDC provider and the CI deploy role (`dispute-intake-ci-deploy`): trust scoped to this repository's own workflows, permissions scoped to ECR push on the two repositories below and to SSM commands against instances tagged for this project — no static AWS keys anywhere (ADR-13). |
| `scripts/02-create-ecr-repos.sh` | `dispute-intake-backend` and `dispute-intake-web`: scan-on-push, immutable tags, untagged images expire after 7 days. Postgres and Metabase use their own official images and need no repository here. |
| `scripts/03-create-instance-role.sh` | The EC2 instance's own role (`dispute-intake-instance`): reachable by Systems Manager (so a deploy needs no SSH key), read access to this project's own SSM path prefix (`/transaction-disputes/prod/*`, where the model API key already lives) and its decryption, pull access to this project's own two ECR repositories, and CloudWatch Logs write. No S3 access: serving never queries the data lake, so this role does not need it. |
| `scripts/04-launch-instance.sh` | A security group open on 80/443 only, a `t3.large` instance in the default VPC with Docker installed by its user data, and a static Elastic IP. |
| `scripts/05-deploy.sh` | Brings the compose stack up on the tagged host over SSM (no SSH): embeds the current `docker-compose.yml`, `docker-compose.prod.yml` and `infra/Caddyfile` in the command; the host reads its own secrets from SSM with its own role, including the demo sign-in access codes when they exist (ADR-18) — absent, sign-in just stays disabled, nothing fails. Prints the sslip.io host name on success. |
| `scripts/06-smoke-test.sh` | Proves the deployed path answers over HTTPS: the health endpoint and the web static page, retrying while Caddy's certificate issuance and the containers' own start-up catch up. Its `--dashboard` flag additionally proves the `dashboard.` subdomain reaches Metabase. |
| `scripts/07-teardown.sh` | Reverses `04-launch-instance.sh`: terminates the tagged instance, releases its Elastic IP, deletes its security group. Leaves the OIDC role, the instance role and the ECR repositories in place. |
| `scripts/08-deploy-metabase.sh` | Creates Metabase's own database and role, sets `analytics_reader`'s password, brings up the `metabase` service, completes its first-run admin setup and connects the `analytics` schema — then swaps in the Caddyfile that routes the `dashboard.` subdomain to it, only once all of that has succeeded (ADR-11). Idempotent: re-running it against an already-provisioned deployment reconciles credentials and the Caddy config without repeating setup. Once Metabase is healthy, it also captures and logs a `docker stats --no-stream` reading of all five services sharing the host (ADR-11's own capacity requirement). |
| `scripts/09-configure-error-alarm.sh` | A CloudWatch metric filter counting error-level lines in the application's log group (`/dispute-intake/app`, created if absent) and an alarm that trips past a threshold in one evaluation window. No notification action is attached yet — no paging channel exists in this project. Authored ahead of log shipping (the CloudWatch agent) landing; running it against the live account is for whichever slice stands that up. |

Every script is idempotent (safe to re-run; an existing resource with the right name is left as
is or reconciled, never duplicated) and refuses to run against any profile or region but
`transaction-disputes` in `us-east-1`. Run them in numeric order; `02` and `03` do not depend on
each other, but `04` needs `03`'s instance profile to exist.

## The deploy pipeline

`.github/workflows/deploy.yml`, triggered manually (`workflow_dispatch`) against `main`: builds
both images, scans each with Trivy (fails the run on a fixable HIGH or CRITICAL finding), pushes
to ECR, runs `05-deploy.sh` (over SSM, no SSH), then `06-smoke-test.sh`. The `teardown_after`
input (default on) runs `07-teardown.sh` at the end — a run gated only by this input and whether
the run was manually cancelled, never by whether an earlier step failed, since a failed deploy or
smoke test is exactly when a host must not be left running unattended; turn it off for a
deployment meant to persist.

The `deploy_metabase` input (default off) adds a job on top of the base deployment: runs
`08-deploy-metabase.sh`, then `06-smoke-test.sh --dashboard`. Left off for a base-stack-only smoke
exercise; turn it on alongside `teardown_after: false` for a deployment meant to persist and carry
the dashboard.

**One-time prerequisites, before the first run:**
- Scripts `01`–`04` already run once against the account.
- The `session-signing-key` SSM parameter exists: `openssl rand -hex 32 |
  infra/scripts/put-secret.sh session-signing-key` (`anthropic-api-key` already does).
- A GitHub Actions repository secret named `AWS_ACCOUNT_ID` holds the account's plain numeric ID,
  so the workflow can compose the CI deploy role's ARN without ever writing the number into this
  repository.

**Optional prerequisites, to turn on real sign-in** (ADR-18) — a deployment without these still
succeeds; the backend just runs with both sign-in brokers disabled, same as every smoke run so
far. Written the same way, via `infra/scripts/put-secret.sh`, all read by `05-deploy.sh`:
- `demo-signin-access-code` — the customer broker's shared secret (at least 16 characters).
  Creating it is what turns `DEMO_SIGNIN_ENABLED` on; there is no separate toggle to set.
- `demo-agent-access-code` and `agent-session-signing-key` — the agent broker's own access code
  and the key that signs agent session tokens (`agent-session-signing-key` needs at least 32
  characters, at least 8 distinct — `openssl rand -hex 32 | infra/scripts/put-secret.sh
  agent-session-signing-key`). Both must exist together to turn `DEMO_AGENT_SIGNIN_ENABLED` on;
  either alone leaves it off.

**Additional one-time prerequisites, before the first run with `deploy_metabase` enabled** — each
written the same way, via `infra/scripts/put-secret.sh`:
- `metabase-db-password` — Metabase's own application-database password.
- `metabase-session-secret-key` — `openssl rand -hex 32 | infra/scripts/put-secret.sh
  metabase-session-secret-key`; without it Metabase signs no session tokens, and anyone with
  access to the application database could forge one.
- `metabase-admin-email` and `metabase-admin-password` — the account `08-deploy-metabase.sh`
  claims on Metabase's first run.
- `analytics-reader-password` — the password `08-deploy-metabase.sh` sets on the `analytics_reader`
  role migration 0004 already created `NOLOGIN` (`pipelines/analytics_load.py`'s own read side),
  and the credential Metabase's `analytics` connection authenticates with.
- Migration 0004 must already be applied against the deployed database (`make migrate`, run with
  `DATABASE_URL` pointed at it) before the first `08-deploy-metabase.sh` run: the script's `ALTER
  ROLE analytics_reader ...` step assumes that role already exists.

## What is deliberately not here

- **Running the scripts against AWS by hand.** They are prepared and reviewed here; the deploy
  workflow is what actually invokes them.
- **The actual secret values** — the demonstration sign-in access codes, the agent
  session-signing key, the Metabase administrator credentials: created under the same
  `/transaction-disputes/prod/*` SSM prefix, the same way, when each is first needed — never in
  this repo. Only the deploy pipeline's own wiring to read them lives here.

## The host name

There is no domain purchase or DNS setup: the deployed address is
`<elastic-ip-with-dots-as-dashes>.sslip.io`, which resolves to the instance's own Elastic IP by
construction. `HOST_NAME` in `Caddyfile` and `docker-compose.prod.yml` is always a configuration
value read from the environment at deploy time, never hardcoded; a real domain replaces it later
by changing that one value. When Metabase is deployed, the dashboard is reachable at
`dashboard.<the same host name>` — sslip.io resolves any subdomain of an IP-based name to that
same IP, so this needs no DNS record of its own.

## The compose stack

`docker-compose.yml` (the repository root) has the Postgres service local development and CI
already use. `docker-compose.prod.yml` adds `backend`, `web` and `caddy` on top of it, without
duplicating Postgres. `docker-compose.metabase.yml` adds the dashboard (ADR-11) on top of that,
in its own database and role, never the default embedded H2:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml config   # validate
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d    # the deploy step runs this
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.metabase.yml \
  up -d metabase   # 08-deploy-metabase.sh runs this, after creating its database and role
```

`ECR_REGISTRY`, `IMAGE_TAG`, `ANTHROPIC_API_KEY`, `SESSION_SIGNING_KEY`, `DEMO_SIGNIN_ACCESS_CODE`,
`DEMO_AGENT_ACCESS_CODE`, `AGENT_SESSION_SIGNING_KEY` and `HOST_NAME` are read from the
environment; the deploy pipeline sets them (each secret by reading its SSM parameter by name,
never printing its value — the three demo sign-in ones resolve to an empty string when their
parameter doesn't exist yet, not an error). Metabase's own `MB_DB_PASS` and `MB_SESSION_SECRET_KEY`
are read the same way, by `08-deploy-metabase.sh`.

`infra/Caddyfile.with-metabase` is the Caddyfile that also routes the dashboard subdomain; it
replaces the plain `infra/Caddyfile` on the host only once Metabase's first-run setup has
succeeded (ADR-11), never before — see `08-deploy-metabase.sh`'s own header for why.

## Capacity

Metabase and its heap share the `t3.large` host with the backend, web, Postgres and the reverse
proxy — no cheaper-instance lever exists, so the memory footprint of all five is measured and
recorded (ADR-11). `08-deploy-metabase.sh` captures a `docker stats --no-stream` reading once
Metabase is healthy and logs it as part of its own run; the first real reading is recorded here
once a deployment with `deploy_metabase` enabled has actually run against the account:

- *(not yet run against the account — record the reading here after the first such deployment)*

A base deployment that later disables `deploy_metabase` still carries the dashboard route only if
`08-deploy-metabase.sh` has run since the last `05-deploy.sh`: that script always writes the plain
`infra/Caddyfile`, which would otherwise silently drop the `dashboard.` route until `08` runs again.

## Images

`Dockerfile` (backend) and `web/Dockerfile` are both multi-stage builds: a builder stage installs
or builds, and the final stage carries only the runtime dependencies. The backend image installs
exactly `pyproject.toml`'s `[project.dependencies]` — none of the `dev`, `data` or `ml` groups
(ADR-6: the `ml` group stays out of the runtime image). Both were built and smoke-tested locally
(`docker build`, then a container run against `/health/live` for the backend and `/` for web).
