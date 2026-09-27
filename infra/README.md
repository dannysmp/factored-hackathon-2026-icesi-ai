# infra/

AWS provisioning for the deployed stack (ADR-13: one EC2 host, ECR, docker compose, GitHub OIDC).

## What these scripts provision

| Script | What it creates |
|---|---|
| `scripts/01-create-oidc-role.sh` | The GitHub Actions OIDC provider and the CI deploy role (`dispute-intake-ci-deploy`): trust scoped to this repository's own workflows, permissions scoped to ECR push on the two repositories below and to SSM commands against instances tagged for this project — no static AWS keys anywhere (ADR-13). |
| `scripts/02-create-ecr-repos.sh` | `dispute-intake-backend` and `dispute-intake-web`: scan-on-push, immutable tags, untagged images expire after 7 days. Postgres and Metabase use their own official images and need no repository here. |
| `scripts/03-create-instance-role.sh` | The EC2 instance's own role (`dispute-intake-instance`): reachable by Systems Manager (so a deploy needs no SSH key), read access to this project's own SSM path prefix (`/transaction-disputes/prod/*`, where the model API key already lives) and its decryption, and CloudWatch Logs write. No S3 access: serving never queries the data lake, so this role does not need it. |
| `scripts/04-launch-instance.sh` | A security group open on 80/443 only, a `t3.large` instance in the default VPC with Docker installed by its user data, and a static Elastic IP. |

Every script is idempotent (safe to re-run; an existing resource with the right name is left as
is or reconciled, never duplicated) and refuses to run against any profile or region but
`transaction-disputes` in `us-east-1`. Run them in numeric order; `02` and `03` do not depend on
each other, but `04` needs `03`'s instance profile to exist.

## What is deliberately not here

- **Running the scripts against AWS.** They are prepared and reviewed here; the two clean-account
  provisioning runs (a later slice, `plan/delivery/streams.md` 3.12) are what actually invokes
  them, on their own scheduled dates, so the account is genuinely empty when each one starts.
- **The deploy pipeline** (build, scan, push, `docker compose pull && up`, smoke test): a later
  slice. These scripts only prepare the host and the identities a deploy needs.
- **Metabase**: its own compose service and provisioning script are a later slice (3.6b).
- **The demonstration sign-in access codes**, the Metabase administrator credentials and any
  secret beyond the model API key: created under the same `/transaction-disputes/prod/*` SSM
  prefix by whichever slice first needs them, following this same pattern — never in this repo.

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
