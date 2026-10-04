#!/usr/bin/env bash
# =============================================================================
# 05-deploy.sh — bring the compose stack up on the host, over SSM
# =============================================================================
# Purpose:
#   The one way the stack reaches the host: no SSH key anywhere. The
#   CI deploy role's own permissions (01-create-oidc-role.sh) are exactly
#   ssm:SendCommand and ssm:GetCommandInvocation against instances tagged for
#   this project — nothing broader.
# Design:
#   The compose files and the Caddyfile live in this repository, not on the
#   host; this script base64-encodes their current content and embeds it in
#   the SSM command, so the host never needs git, a checkout or a token to
#   fetch them. Every application secret (the model API key, the session-
#   signing key, the Postgres password, and — when present — the demo
#   sign-in access codes and the agent session-signing key) is read on the
#   host itself, by the host's own instance role (03-create-instance-role.sh)
#   — this script and the CI role that calls it never see any of their
#   values, matching PII/secret minimization.
#   `postgres-password` is mandatory, the same way anthropic-api-key and
#   session-signing-key already are (issue #149: the base compose file's own
#   `POSTGRES_PASSWORD:-dispute_intake` default exists only for local
#   development with nothing SSM-backed exported; a deployed stack must never
#   fall through to it, so this script fails loudly, before ever running
#   `docker compose up`, if the parameter doesn't exist). `POSTGRES_PASSWORD`
#   only takes effect on Postgres's own first init of an empty data
#   directory — on a redeploy against an already-initialized volume (any
#   `teardown_after: false` run, which is exactly what a persisting preview
#   or evaluation deployment is), the official image silently ignores it, so
#   this script also rotates the live role's real password after the stack
#   is up, the same `psql -v pw=... ALTER ROLE ... PASSWORD :'pw'` pattern
#   08-deploy-metabase.sh already established for the Metabase-side roles —
#   piped over stdin, never `-c`, since `:'var'` substitution only takes
#   effect that way (verified against a real Postgres server, not assumed).
#   The three demo sign-in parameters are optional: a deployment
#   where the maintainer hasn't created them yet (the smoke-only path) gets
#   an empty value for each and sign-in stays disabled, exactly as before
#   this script knew about them — `DEMO_SIGNIN_ENABLED`/
#   `DEMO_AGENT_SIGNIN_ENABLED` are derived on the host from whether the
#   corresponding access code resolved to a non-empty value, not from a
#   separate toggle this script or its caller would need to remember to set.
#   Resolving one of the three distinguishes the parameter genuinely not
#   existing yet (`ParameterNotFound` — the smoke-only path, expected, an
#   empty value) from any other AWS error (access denied, throttling, a
#   typo'd name) — those still fail the deploy loudly, before ever running
#   `docker compose up`, the same as the two mandatory secrets above,
#   instead of silently landing on "sign-in stays disabled" for a reason
#   that has nothing to do with the parameter not being created. The
#   resulting `DEMO_SIGNIN_ENABLED`/`DEMO_AGENT_SIGNIN_ENABLED` values are
#   logged (never the secret values) once computed, so a maintainer reading
#   this command's own output knows the sign-in state without needing to
#   separately test it.
#   After the stack is up, this script also migrates and seeds the database:
#   the host syncs the already-built operational seed (three Parquet files
#   and its manifest, never committed — `data/` is git-ignored) from this
#   project's own seed bucket (11-create-seed-bucket.sh), using the host's
#   own instance role — the CI role that calls this script never touches the
#   seed, same secret-minimization boundary as every value above. Migrations
#   and the load itself run with `docker compose run --rm`, a fresh one-off
#   container using the backend image with a different command, never
#   `docker compose exec` against the long-running `backend` service: that
#   service's own startup (`app.main.create_app`) validates every demo
#   persona against a seeded customer when demo sign-in is enabled
#   and fails closed before binding a port if the database is still empty —
#   exactly the state right after `up -d` on a freshly launched instance —
#   so `backend` would still be crash-looping, unreachable by `exec`, at the
#   moment this step needs to run. `load_seed` itself is the one thing that
#   verifies the seed's checksum against its manifest and applies the
#   privacy-scanned pipeline output atomically (one transaction, truncate
#   then reload) — this script only ever moves the already-built artifact
#   and invokes that loader, never re-derives or re-touches the data. The
#   explicit `restart backend` after seeding is what turns a still
#   crash-looping container into a healthy one on this same run, rather than
#   waiting out Docker's own exponential restart backoff.
#   Idempotent: `docker compose up -d` reconciles a running stack to the new
#   image tag rather than erroring on one already up; `load_seed` truncates
#   and reloads in one transaction, so a redeploy against an already-seeded
#   database never duplicates rows.
# Usage:
#   IMAGE_TAG=<sha> infra/scripts/05-deploy.sh
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

require_profile
require_live_credentials

: "${IMAGE_TAG:?IMAGE_TAG must be set}"

REPO_ROOT="$(cd ../.. && pwd)"
readonly REPO_ROOT
readonly SSM_SECRET_PREFIX="/transaction-disputes/prod"

# Resolved here, not taken as an input: GitHub Actions silently blanks a job output whose value
# contains a registered secret ("Skip output '...' since it may contain secret"), and it matches
# by value, not by where the value came from — passing this account-id-derived string as a
# cross-job output is silently unusable once AWS_ACCOUNT_ID is a registered secret. A step within
# the same job that builds the identical string is unaffected, since masking only blocks the
# cross-job propagation. Resolving it fresh here sidesteps that rather than fighting it.
account_id="$(aws sts get-caller-identity --query Account --output text)"
readonly ECR_REGISTRY="${account_id}.dkr.ecr.${INFRA_REGION}.amazonaws.com"
readonly SEED_BUCKET="dispute-intake-ops-seed-${account_id}"

instance_id="$(aws ec2 describe-instances \
  --filters "Name=tag:${INFRA_TAG_KEY},Values=${INFRA_TAG_VALUE}" "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].InstanceId" --output text)"
if [[ -z "${instance_id}" ]]; then
  log "refusing: no running instance is tagged ${INFRA_TAG_KEY}=${INFRA_TAG_VALUE}; run 04-launch-instance.sh first"
  exit 1
fi

public_ip="$(aws ec2 describe-addresses \
  --filters "Name=tag:${INFRA_TAG_KEY},Values=${INFRA_TAG_VALUE}" \
  --query "Addresses[0].PublicIp" --output text)"
if [[ "${public_ip}" == "None" || -z "${public_ip}" ]]; then
  log "refusing: no Elastic IP is tagged ${INFRA_TAG_KEY}=${INFRA_TAG_VALUE}; run 04-launch-instance.sh first"
  exit 1
fi
readonly HOST_NAME="${public_ip//./-}.sslip.io"

compose_base_b64="$(base64 <"${REPO_ROOT}/docker-compose.yml" | tr -d '\n')"
compose_prod_b64="$(base64 <"${REPO_ROOT}/docker-compose.prod.yml" | tr -d '\n')"
caddyfile_b64="$(base64 <"${REPO_ROOT}/infra/Caddyfile" | tr -d '\n')"

# Deliberately an unquoted heredoc: every `${...}` below is substituted now, on the CI runner,
# baking the file contents and the known values directly into the script text the host receives.
# The two `\$(...)` command substitutions are escaped so they run later, on the host itself, once
# this script reaches it over SSM — never here, and never with this role's credentials.
remote_script="$(cat <<SCRIPT
set -euo pipefail
mkdir -p /opt/dispute-intake/infra
echo '${compose_base_b64}' | base64 -d >/opt/dispute-intake/docker-compose.yml
echo '${compose_prod_b64}' | base64 -d >/opt/dispute-intake/docker-compose.prod.yml
echo '${caddyfile_b64}' | base64 -d >/opt/dispute-intake/infra/Caddyfile
cd /opt/dispute-intake
export ECR_REGISTRY='${ECR_REGISTRY}'
export IMAGE_TAG='${IMAGE_TAG}'
export HOST_NAME='${HOST_NAME}'
# A plain assignment on its own line, then a separate "export VAR", not "export VAR=\"\$(...)\"":
# combined with export (or local/declare/readonly), the exit status of a failing command
# substitution is masked by the exit status of export itself, so "set -e" would never see the
# failure and would carry on with an empty value instead of refusing the deploy.
ANTHROPIC_API_KEY="\$(aws ssm get-parameter --name ${SSM_SECRET_PREFIX}/anthropic-api-key --with-decryption --query Parameter.Value --output text)"
export ANTHROPIC_API_KEY
SESSION_SIGNING_KEY="\$(aws ssm get-parameter --name ${SSM_SECRET_PREFIX}/session-signing-key --with-decryption --query Parameter.Value --output text)"
export SESSION_SIGNING_KEY
POSTGRES_PASSWORD="\$(aws ssm get-parameter --name ${SSM_SECRET_PREFIX}/postgres-password --with-decryption --query Parameter.Value --output text)"
export POSTGRES_PASSWORD
resolve_optional_secret() {
  # "ParameterNotFound" is a stable error-type token the AWS CLI itself defines, not a
  # locale-dependent message string -- present in its output the same way regardless of
  # AWS_DEFAULT_OUTPUT or the locale in use, so matching it directly is safe. A fresh mktemp
  # per call, rather than a fixed path, means concurrent calls (the three optional secrets, if
  # this were ever parallelized) never share an error file, so none can overwrite what another
  # wrote.
  local name="\$1" value err_file
  err_file="\$(mktemp)"
  if value="\$(aws ssm get-parameter --name ${SSM_SECRET_PREFIX}/"\${name}" --with-decryption \
      --query Parameter.Value --output text 2>"\${err_file}")"; then
    rm -f "\${err_file}"
    printf '%s' "\${value}"
    return 0
  fi
  if grep -q "ParameterNotFound" "\${err_file}"; then
    rm -f "\${err_file}"
    printf ''
    return 0
  fi
  echo "refusing: unexpected error reading optional secret '\${name}':" >&2
  cat "\${err_file}" >&2
  rm -f "\${err_file}"
  exit 1
}
# Same split-assignment reason as above: "export VAR=\"\$(resolve_optional_secret ...)\"" would
# mask the "exit 1" resolve_optional_secret itself returns on a genuine SSM error, hiding it
# behind the exit status of export.
DEMO_SIGNIN_ACCESS_CODE="\$(resolve_optional_secret demo-signin-access-code)"
export DEMO_SIGNIN_ACCESS_CODE
DEMO_AGENT_ACCESS_CODE="\$(resolve_optional_secret demo-agent-access-code)"
export DEMO_AGENT_ACCESS_CODE
AGENT_SESSION_SIGNING_KEY="\$(resolve_optional_secret agent-session-signing-key)"
export AGENT_SESSION_SIGNING_KEY
export DEMO_SIGNIN_ENABLED=false
if [ -n "\${DEMO_SIGNIN_ACCESS_CODE}" ]; then export DEMO_SIGNIN_ENABLED=true; fi
export DEMO_AGENT_SIGNIN_ENABLED=false
if [ -n "\${DEMO_AGENT_ACCESS_CODE}" ] && [ -n "\${AGENT_SESSION_SIGNING_KEY}" ]; then export DEMO_AGENT_SIGNIN_ENABLED=true; fi
echo "demo sign-in state: DEMO_SIGNIN_ENABLED=\${DEMO_SIGNIN_ENABLED} DEMO_AGENT_SIGNIN_ENABLED=\${DEMO_AGENT_SIGNIN_ENABLED}"
aws ecr get-login-password --region ${INFRA_REGION} | docker login --username AWS --password-stdin "\${ECR_REGISTRY}"
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
pg_role="\${POSTGRES_USER:-dispute_intake}"
pg_db="\${POSTGRES_DB:-dispute_intake}"
for _ in \$(seq 1 10); do
  docker compose exec -T postgres pg_isready -U "\${pg_role}" >/dev/null 2>&1 && break
  sleep 3
done
echo "ALTER ROLE \${pg_role} PASSWORD :'pw'" | docker compose exec -T postgres \
  psql -v ON_ERROR_STOP=1 -v pw="\${POSTGRES_PASSWORD}" -U "\${pg_role}" -d "\${pg_db}"
mkdir -p /opt/dispute-intake/seed/ops_seed
aws s3 sync "s3://${SEED_BUCKET}/ops_seed/" /opt/dispute-intake/seed/ops_seed/
chmod -R a+rX /opt/dispute-intake/seed
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm -T backend \
  python -m app.persistence.migrate
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm -T backend \
  python -m app.persistence.load_seed
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart backend
SCRIPT
)"

params_file="$(mktemp)"
trap 'rm -f "${params_file}"' EXIT
jq -n --arg script "${remote_script}" '{commands: [$script]}' >"${params_file}"

log "sending the deploy command to ${instance_id}"
command_id="$(aws ssm send-command \
  --instance-ids "${instance_id}" \
  --document-name "AWS-RunShellScript" \
  --parameters "file://${params_file}" \
  --query "Command.CommandId" --output text)"

log "waiting for the deploy command to finish"
for _ in $(seq 1 30); do
  status="$(aws ssm get-command-invocation \
    --command-id "${command_id}" --instance-id "${instance_id}" \
    --query "Status" --output text 2>/dev/null || echo "Pending")"
  case "${status}" in
    Success) log "deploy command succeeded"; echo "${HOST_NAME}"; exit 0 ;;
    Failed|Cancelled|TimedOut)
      log "deploy command ${status}; output follows"
      aws ssm get-command-invocation --command-id "${command_id}" --instance-id "${instance_id}" \
        --query "StandardOutputContent" --output text
      aws ssm get-command-invocation --command-id "${command_id}" --instance-id "${instance_id}" \
        --query "StandardErrorContent" --output text >&2
      exit 1
      ;;
    *) sleep 10 ;;
  esac
done
log "refusing: deploy command did not finish within 5 minutes"
exit 1
