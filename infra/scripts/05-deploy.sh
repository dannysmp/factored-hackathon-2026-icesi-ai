#!/usr/bin/env bash
# =============================================================================
# 05-deploy.sh — bring the compose stack up on the host, over SSM
# =============================================================================
# Purpose:
#   The one way the stack reaches the host: no SSH key anywhere (ADR-13). The
#   CI deploy role's own permissions (01-create-oidc-role.sh) are exactly
#   ssm:SendCommand and ssm:GetCommandInvocation against instances tagged for
#   this project — nothing broader.
# Design:
#   The compose files and the Caddyfile live in this repository, not on the
#   host; this script base64-encodes their current content and embeds it in
#   the SSM command, so the host never needs git, a checkout or a token to
#   fetch them. The two application secrets (the model API key, the session-
#   signing key) are read on the host itself, by the host's own instance
#   role (03-create-instance-role.sh) — this script and the CI role that
#   calls it never see either value, matching PII/secret minimization.
#   Idempotent: `docker compose up -d` reconciles a running stack to the new
#   image tag rather than erroring on one already up.
# Usage:
#   ECR_REGISTRY=<account>.dkr.ecr.us-east-1.amazonaws.com IMAGE_TAG=<sha> \
#     infra/scripts/05-deploy.sh
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

require_profile
require_live_credentials

: "${ECR_REGISTRY:?ECR_REGISTRY must be set}"
: "${IMAGE_TAG:?IMAGE_TAG must be set}"

REPO_ROOT="$(cd ../.. && pwd)"
readonly REPO_ROOT
readonly SSM_SECRET_PREFIX="/transaction-disputes/prod"

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
export ANTHROPIC_API_KEY="\$(aws ssm get-parameter --name ${SSM_SECRET_PREFIX}/anthropic-api-key --with-decryption --query Parameter.Value --output text)"
export SESSION_SIGNING_KEY="\$(aws ssm get-parameter --name ${SSM_SECRET_PREFIX}/session-signing-key --with-decryption --query Parameter.Value --output text)"
aws ecr get-login-password --region ${INFRA_REGION} | docker login --username AWS --password-stdin "\${ECR_REGISTRY}"
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
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
