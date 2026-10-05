#!/usr/bin/env bash
# =============================================================================
# 13-verify-analytics.sh — the dashboard's tables must hold data
# =============================================================================
# Purpose:
#   Fails when any table an Operations dashboard question reads is missing or
#   empty in the deployed database, so a deployment whose analytics load did
#   not happen cannot pass quietly and leave every panel on "No results".
# Design:
#   The tables are not listed here: they are the `mart` of each panel in
#   `lib/theme_metabase_dashboard.py`, the one place the dashboard's questions
#   are defined, read with that module's own `--list-marts` mode. The count
#   runs on the host over SSM, inside the database container, so nothing here
#   needs a database credential or a network path to the database. Only row
#   counts are printed. Only the tables a dashboard question reads are checked;
#   a loaded table no question reads is not.
# Usage:
#   infra/scripts/13-verify-analytics.sh
#   (after 05-deploy.sh, which loads the analytics schema)
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

require_profile
require_live_credentials

marts="$(python3 lib/theme_metabase_dashboard.py --list-marts)"
if [[ -z "${marts}" ]]; then
  log "refusing: the dashboard definition lists no tables to verify"
  exit 1
fi
while IFS= read -r mart; do
  if [[ ! "${mart}" =~ ^[a-z_]+$ ]]; then
    log "refusing: '${mart}' is not a plain table name"
    exit 1
  fi
done <<<"${marts}"

instance_id="$(aws ec2 describe-instances \
  --filters "Name=tag:${INFRA_TAG_KEY},Values=${INFRA_TAG_VALUE}" "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].InstanceId" --output text)"
if [[ -z "${instance_id}" ]]; then
  log "refusing: no running instance is tagged ${INFRA_TAG_KEY}=${INFRA_TAG_VALUE}; run 05-deploy.sh first"
  exit 1
fi

# Unquoted heredoc: `${marts}` is substituted now; every `\$...` runs later, on the host.
remote_script="$(cat <<SCRIPT
set -euo pipefail
cd /opt/dispute-intake
pg_role="\${POSTGRES_USER:-dispute_intake}"
pg_db="\${POSTGRES_DB:-dispute_intake}"
empty=0
for mart in ${marts//$'\n'/ }; do
  rows="\$(docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "\${pg_role}" -d "\${pg_db}" \
    -Atc "SELECT count(*) FROM analytics.\${mart}")"
  echo "analytics.\${mart}: \${rows} rows"
  if [ "\${rows}" -lt 1 ]; then empty=1; fi
done
if [ "\${empty}" -ne 0 ]; then
  echo "refusing: a table behind the dashboard holds no rows" >&2
  exit 1
fi
SCRIPT
)"

params_file="$(mktemp)"
trap 'rm -f "${params_file}"' EXIT
jq -n --arg script "${remote_script}" '{commands: [$script]}' >"${params_file}"

log "sending the analytics check to ${instance_id}"
command_id="$(aws ssm send-command \
  --instance-ids "${instance_id}" \
  --document-name "AWS-RunShellScript" \
  --parameters "file://${params_file}" \
  --query "Command.CommandId" --output text)"

log "waiting for the analytics check to finish"
for _ in $(seq 1 30); do
  status="$(aws ssm get-command-invocation \
    --command-id "${command_id}" --instance-id "${instance_id}" \
    --query "Status" --output text 2>/dev/null || echo "Pending")"
  case "${status}" in
    Success)
      log "every table behind the dashboard holds rows"
      aws ssm get-command-invocation --command-id "${command_id}" --instance-id "${instance_id}" \
        --query "StandardOutputContent" --output text
      exit 0
      ;;
    Failed|Cancelled|TimedOut)
      log "analytics check ${status}; output follows"
      aws ssm get-command-invocation --command-id "${command_id}" --instance-id "${instance_id}" \
        --query "StandardOutputContent" --output text
      aws ssm get-command-invocation --command-id "${command_id}" --instance-id "${instance_id}" \
        --query "StandardErrorContent" --output text >&2
      exit 1
      ;;
    *) sleep 10 ;;
  esac
done
log "refusing: the analytics check did not finish within 5 minutes"
exit 1
