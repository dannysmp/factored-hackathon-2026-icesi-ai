#!/usr/bin/env bash
# =============================================================================
# 10-configure-metabase-dashboard.sh — style the operations dashboard's panels
# =============================================================================
# Purpose:
#   Creates or updates the operations dashboard's panels, coloring each chart
#   from the design-token palette and pairing it with a text card naming its
#   business question (the open-source edition's own theming surface — full
#   native theming, logo, app name, instance-wide colors, is Enterprise-only;
#   see docs/limitations.md). A separate script from
#   08-deploy-metabase.sh on purpose: that script's own stated job is
#   narrowly "admin claimed before Caddy sees it," not dashboard content, and
#   folding this into it would risk re-running dashboard-content logic every
#   time 08 defensively re-runs its own unrelated setup checks.
# Design:
#   All the API orchestration lives in lib/theme_metabase_dashboard.py, stdlib
#   only, run wherever it can reach Metabase — the deployed host over SSM
#   here (Metabase's port is loopback-only, same constraint 08 documents), or
#   directly against a local container during development. That file is
#   embedded into the remote command the same way 08 already embeds the
#   Caddyfile, so the exact code that was tested locally is what runs on the
#   host, not a re-transcription of it.
#   It also removes the sample content Metabase ships with (its sample
#   database and the example collections and dashboards), so the instance
#   carries only the Operations dashboard, and runs each panel's question,
#   failing when one returns no rows.
#   Idempotent: every card and the dashboard are checked by name before being
#   created, and updated in place if found, so a redeploy converges to
#   exactly the panels the script defines rather than duplicating them.
# Usage:
#   infra/scripts/10-configure-metabase-dashboard.sh
#   (needs 08-deploy-metabase.sh already run: an admin account and the
#   analytics datasource connection must already exist)
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

require_profile
require_live_credentials

REPO_ROOT="$(cd ../.. && pwd)"
readonly REPO_ROOT
readonly SSM_SECRET_PREFIX="/transaction-disputes/prod"

instance_id="$(aws ec2 describe-instances \
  --filters "Name=tag:${INFRA_TAG_KEY},Values=${INFRA_TAG_VALUE}" "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].InstanceId" --output text)"
if [[ -z "${instance_id}" ]]; then
  log "refusing: no running instance is tagged ${INFRA_TAG_KEY}=${INFRA_TAG_VALUE}; run 05-deploy.sh first"
  exit 1
fi

theme_script_b64="$(base64 <"${REPO_ROOT}/infra/scripts/lib/theme_metabase_dashboard.py" | tr -d '\n')"

# Unquoted heredoc: every `${...}` below is substituted now, baking the Python script's content
# directly into the script text the host receives, the same convention 08-deploy-metabase.sh
# uses for the Caddyfile. Every `\$(...)` is escaped so it runs later, on the host, never here.
remote_script="$(cat <<SCRIPT
set -euo pipefail
command -v python3 >/dev/null 2>&1 || dnf install -y python3 >/dev/null
cd /opt/dispute-intake

admin_email="\$(aws ssm get-parameter --name ${SSM_SECRET_PREFIX}/metabase-admin-email --with-decryption --query Parameter.Value --output text)"
admin_password="\$(aws ssm get-parameter --name ${SSM_SECRET_PREFIX}/metabase-admin-password --with-decryption --query Parameter.Value --output text)"

echo '${theme_script_b64}' | base64 -d >/tmp/theme_metabase_dashboard.py

MB_BASE_URL="http://localhost:3000" MB_ADMIN_EMAIL="\${admin_email}" MB_ADMIN_PASSWORD="\${admin_password}" \
  python3 /tmp/theme_metabase_dashboard.py
rm -f /tmp/theme_metabase_dashboard.py
SCRIPT
)"

params_file="$(mktemp)"
trap 'rm -f "${params_file}"' EXIT
jq -n --arg script "${remote_script}" '{commands: [$script]}' >"${params_file}"

log "sending the dashboard theming command to ${instance_id}"
command_id="$(aws ssm send-command \
  --instance-ids "${instance_id}" \
  --document-name "AWS-RunShellScript" \
  --parameters "file://${params_file}" \
  --query "Command.CommandId" --output text)"

log "waiting for the dashboard theming command to finish"
for _ in $(seq 1 60); do
  status="$(aws ssm get-command-invocation \
    --command-id "${command_id}" --instance-id "${instance_id}" \
    --query "Status" --output text 2>/dev/null || echo "Pending")"
  case "${status}" in
    Success)
      log "dashboard theming succeeded; writing the checklist it printed"
      notes="$(aws ssm get-command-invocation --command-id "${command_id}" --instance-id "${instance_id}" \
        --query "StandardErrorContent" --output text)"
      if [[ -n "${notes}" && "${notes}" != "None" ]]; then
        printf '%s\n' "${notes}" >&2
      fi
      aws ssm get-command-invocation --command-id "${command_id}" --instance-id "${instance_id}" \
        --query "StandardOutputContent" --output text >"${REPO_ROOT}/reports/dashboard-theme-checklist.md"
      cat "${REPO_ROOT}/reports/dashboard-theme-checklist.md"
      exit 0
      ;;
    Failed|Cancelled|TimedOut)
      log "dashboard theming ${status}; output follows"
      aws ssm get-command-invocation --command-id "${command_id}" --instance-id "${instance_id}" \
        --query "StandardOutputContent" --output text
      aws ssm get-command-invocation --command-id "${command_id}" --instance-id "${instance_id}" \
        --query "StandardErrorContent" --output text >&2
      exit 1
      ;;
    *) sleep 10 ;;
  esac
done
log "refusing: dashboard theming did not finish within 10 minutes"
exit 1
