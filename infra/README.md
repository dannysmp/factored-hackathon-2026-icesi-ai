# infra/

AWS provisioning for the deployed stack (ADR-13: one EC2 host, ECR, docker compose, GitHub OIDC).

## What these scripts provision

| Script | What it creates |
|---|---|
| `scripts/01-create-oidc-role.sh` | The GitHub Actions OIDC provider and the CI deploy role (`dispute-intake-ci-deploy`): trust scoped to this repository's own workflows, permissions scoped to ECR push on the two repositories below and to SSM commands against instances tagged for this project — no static AWS keys anywhere (ADR-13). |
| `scripts/02-create-ecr-repos.sh` | `dispute-intake-backend` and `dispute-intake-web`: scan-on-push, immutable tags, untagged images expire after 7 days. Postgres and Metabase use their own official images and need no repository here. |
| `scripts/03-create-instance-role.sh` | The EC2 instance's own role (`dispute-intake-instance`): reachable by Systems Manager (so a deploy needs no SSH key), read access to this project's own SSM path prefix (`/transaction-disputes/prod/*`, where the model API key already lives) and its decryption, pull access to this project's own two ECR repositories, and CloudWatch Logs write. No S3 access: serving never queries the data lake, so this role does not need it. |
| `scripts/04-launch-instance.sh` | A security group open on 80/443 only, a `t3.large` instance in the default VPC with Docker installed by its user data, and a static Elastic IP. |
| `scripts/05-deploy.sh` | Brings the compose stack up on the tagged host over SSM (no SSH): embeds the current `docker-compose.yml`, `docker-compose.prod.yml` and `infra/Caddyfile` in the command; the host reads its own two secrets from SSM with its own role. Prints the sslip.io host name on success. |
| `scripts/06-smoke-test.sh` | Proves the deployed path answers over HTTPS: the health endpoint and the web static page, retrying while Caddy's certificate issuance and the containers' own start-up catch up. |
| `scripts/07-teardown.sh` | Reverses `04-launch-instance.sh`: terminates the tagged instance, releases its Elastic IP, deletes its security group. Leaves the OIDC role, the instance role and the ECR repositories in place. |

Every script is idempotent (safe to re-run; an existing resource with the right name is left as
is or reconciled, never duplicated) and refuses to run against any profile or region but
`transaction-disputes` in `us-east-1`. Run them in numeric order; `02` and `03` do not depend on
each other, but `04` needs `03`'s instance profile to exist.

## The deploy pipeline

`.github/workflows/deploy.yml`, triggered manually (`workflow_dispatch`) against `main`: builds
both images, scans each with Trivy (fails the run on a fixable HIGH or CRITICAL finding), pushes
to ECR, runs `05-deploy.sh` (over SSM, no SSH), then `06-smoke-test.sh`. The `teardown_after`
input (default on) runs `07-teardown.sh` at the end, matching the early smoke exercise's own
requirement (`plan/delivery/streams.md` 3.12a: "torn down after the run"); turn it off for a
deployment meant to persist (the two later, full functional-deployment runs).

**One-time prerequisites, before the first run:**
- Scripts `01`–`04` already run once against the account.
- The `session-signing-key` SSM parameter exists: `openssl rand -hex 32 |
  infra/scripts/put-secret.sh session-signing-key` (`anthropic-api-key` already does).
- A GitHub Actions repository secret named `AWS_ACCOUNT_ID` holds the account's plain numeric ID,
  so the workflow can compose the CI deploy role's ARN without ever writing the number into this
  repository.

## What is deliberately not here

- **Running the scripts against AWS by hand.** They are prepared and reviewed here; the deploy
  workflow and the two clean-account provisioning runs (`plan/delivery/streams.md` 3.12) are what
  actually invoke them.
- **Metabase**: its own compose service and provisioning script are a later slice (3.6b).
- **The demonstration sign-in access codes** and the Metabase administrator credentials: created
  under the same `/transaction-disputes/prod/*` SSM prefix by whichever slice first needs them,
  following the same `put-secret.sh` pattern — never in this repo.

## The host name

There is no domain purchase or DNS setup: the deployed address is
`<elastic-ip-with-dots-as-dashes>.sslip.io`, which resolves to the instance's own Elastic IP by
construction. `HOST_NAME` in `Caddyfile` and `docker-compose.prod.yml` is always a configuration
value read from the environment at deploy time, never hardcoded; a real domain replaces it later
by changing that one value.

## The compose stack

`docker-compose.yml` (the repository root) has the Postgres service local development and CI
already use. `docker-compose.prod.yml` adds `backend`, `web` and `caddy` on top of it, without
duplicating Postgres:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml config   # validate
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d    # the deploy step runs this
```

`ECR_REGISTRY`, `IMAGE_TAG`, `ANTHROPIC_API_KEY`, `SESSION_SIGNING_KEY` and `HOST_NAME` are read
from the environment; the deploy pipeline sets them (the two secrets by reading their SSM
parameters by name, never printing either value).

## Images

`Dockerfile` (backend) and `web/Dockerfile` are both multi-stage builds: a builder stage installs
or builds, and the final stage carries only the runtime dependencies. The backend image installs
exactly `pyproject.toml`'s `[project.dependencies]` — none of the `dev`, `data` or `ml` groups
(ADR-6: the `ml` group stays out of the runtime image). Both were built and smoke-tested locally
(`docker build`, then a container run against `/health/live` for the backend and `/` for web).
