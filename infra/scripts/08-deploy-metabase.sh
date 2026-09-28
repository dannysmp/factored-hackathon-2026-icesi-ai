#!/usr/bin/env bash
# =============================================================================
# 08-deploy-metabase.sh — stand up Metabase, admin claimed before Caddy sees it
# =============================================================================
# Purpose:
#   Runs after 05-deploy.sh has the base stack up. Creates Metabase's own
#   database and role, brings up its container, completes its first-run
#   setup and connects the analytics schema, then — only once that succeeds
#   — swaps in the Caddyfile that can reach it (ADR-11's own hardening: "the
#   first-run setup... is completed by the provisioning script before the
#   reverse proxy exposes Metabase"). Also sets analytics_reader's password:
#   migration 0004 created that role `NOLOGIN`, deferring its password to
#   whichever consumer first needs to log in as it — Metabase is that
#   consumer.
# Design:
#   Over SSM, no SSH, the same transport 05-deploy.sh uses. Idempotent:
#   Metabase's own `/api/setup` refuses a second call once an admin exists
#   (its own token is consumed the first time), so a re-run of this script
#   against an already-provisioned deployment skips straight to reconciling
#   the Caddy config; the role/database/analytics-connection steps check
#   for existence first, the same convention every other script here uses.
#   Metabase's setup token is returned by its own unauthenticated
#   `/api/session/properties` endpoint — reachable by anyone who can reach
#   the container, verified directly against a real local instance while
#   building this script — which is exactly why the container's port is
#   loopback-only and Caddy never routes to it until setup has already
#   claimed that token. Once Metabase is healthy, the script also captures
#   and logs the memory footprint of the five services now sharing the
#   host, per ADR-11's own capacity requirement.
# Usage:
#   infra/scripts/08-deploy-metabase.sh
#   (needs 05-deploy.sh already run, and the SSM parameters this script
#   reads already set: metabase-db-password, metabase-session-secret-key,
#   metabase-admin-email, metabase-admin-password, analytics-reader-password)
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

require_profile
require_live_credentials

REPO_ROOT="$(cd ../.. && pwd)"
readonly REPO_ROOT
readonly SSM_SECRET_PREFIX="/transaction-disputes/prod"
readonly APP_DB_NAME="dispute_intake"

instance_id="$(aws ec2 describe-instances \
  --filters "Name=tag:${INFRA_TAG_KEY},Values=${INFRA_TAG_VALUE}" "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].InstanceId" --output text)"
if [[ -z "${instance_id}" ]]; then
  log "refusing: no running instance is tagged ${INFRA_TAG_KEY}=${INFRA_TAG_VALUE}; run 05-deploy.sh first"
  exit 1
fi

caddyfile_with_metabase_b64="$(base64 <"${REPO_ROOT}/infra/Caddyfile.with-metabase" | tr -d '\n')"

# Unquoted heredoc: every `${...}` below is substituted now, baking the Caddyfile content
# directly into the script text the host receives, the same convention 05-deploy.sh uses. Every
# `\$(...)` is escaped so it runs later, on the host, never here.
remote_script="$(cat <<SCRIPT
set -euo pipefail
command -v curl >/dev/null 2>&1 || dnf install -y curl >/dev/null
command -v python3 >/dev/null 2>&1 || dnf install -y python3 >/dev/null
cd /opt/dispute-intake

db_password="\$(aws ssm get-parameter --name ${SSM_SECRET_PREFIX}/metabase-db-password --with-decryption --query Parameter.Value --output text)"
session_key="\$(aws ssm get-parameter --name ${SSM_SECRET_PREFIX}/metabase-session-secret-key --with-decryption --query Parameter.Value --output text)"
admin_email="\$(aws ssm get-parameter --name ${SSM_SECRET_PREFIX}/metabase-admin-email --with-decryption --query Parameter.Value --output text)"
admin_password="\$(aws ssm get-parameter --name ${SSM_SECRET_PREFIX}/metabase-admin-password --with-decryption --query Parameter.Value --output text)"
analytics_reader_password="\$(aws ssm get-parameter --name ${SSM_SECRET_PREFIX}/analytics-reader-password --with-decryption --query Parameter.Value --output text)"

psql_exec() {
  docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U dispute_intake -d ${APP_DB_NAME} -tAc "\$1"
}

# Binds the password as a psql variable and lets psql's own \`:'pw'\` substitution apply SQL-literal
# quoting, rather than splicing the value into the SQL text: a password containing a single quote
# (an ordinary character in one) would otherwise break out of the string it was meant to sit inside.
# Piped over stdin, not \`-c\`: psql only performs \`:'var'\` interpolation when reading a script,
# never inside a \`-c\` argument (verified directly — \`-c\` sends the text to the server unprocessed).
psql_set_password() {
  echo "\$1" | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -v pw="\$2" -U dispute_intake -d ${APP_DB_NAME}
}

role_exists="\$(psql_exec "SELECT 1 FROM pg_roles WHERE rolname = 'metabase_app'")" \
  || { echo "failed to check whether role metabase_app exists" >&2; exit 1; }
if [ -z "\${role_exists}" ]; then
  echo "creating role metabase_app"
  psql_set_password "CREATE ROLE metabase_app LOGIN PASSWORD :'pw'" "\${db_password}"
else
  echo "role metabase_app already exists; reconciling its password"
  psql_set_password "ALTER ROLE metabase_app PASSWORD :'pw'" "\${db_password}"
fi

database_exists="\$(psql_exec "SELECT 1 FROM pg_database WHERE datname = 'metabase'")" \
  || { echo "failed to check whether database metabase exists" >&2; exit 1; }
if [ -z "\${database_exists}" ]; then
  echo "creating database metabase"
  psql_exec "CREATE DATABASE metabase OWNER metabase_app"
fi

echo "setting analytics_reader's password and enabling login"
psql_set_password "ALTER ROLE analytics_reader LOGIN PASSWORD :'pw'" "\${analytics_reader_password}"

echo "starting the metabase container"
export MB_DB_PASS="\${db_password}"
export MB_SESSION_SECRET_KEY="\${session_key}"
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.metabase.yml up -d metabase

echo "waiting for metabase to become healthy"
for i in \$(seq 1 40); do
  code="\$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/api/health || echo 000)"
  [ "\$code" = "200" ] && break
  sleep 5
done
if [ "\$code" != "200" ]; then
  echo "metabase did not become healthy in time" >&2
  exit 1
fi

echo "memory footprint of the five services on the host:"
docker stats --no-stream --format '{{.Name}}: {{.MemUsage}}'

has_user_setup="\$(curl -s http://localhost:3000/api/session/properties | python3 -c 'import json,sys; print(json.load(sys.stdin)["has-user-setup"])')"
if [ "\$has_user_setup" = "True" ]; then
  echo "metabase admin already set up, skipping /api/setup"
else
  echo "claiming the metabase admin account"
  setup_token="\$(curl -s http://localhost:3000/api/session/properties | python3 -c 'import json,sys; print(json.load(sys.stdin)["setup-token"])')"
  setup_response="\$(curl -s -X POST http://localhost:3000/api/setup \
    -H 'Content-Type: application/json' \
    -d "\$(python3 -c 'import json,os,sys; print(json.dumps({"token": sys.argv[1], "user": {"email": sys.argv[2], "password": sys.argv[3], "first_name": "Dispute", "last_name": "Admin"}, "prefs": {"site_name": "Transaction Disputes", "allow_tracking": False}}))' "\${setup_token}" "\${admin_email}" "\${admin_password}")")"
  echo "\${setup_response}" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert "id" in d, f"setup failed: {d}"'
fi

session_id="\$(curl -s -X POST http://localhost:3000/api/session \
  -H 'Content-Type: application/json' \
  -d "\$(python3 -c 'import json,sys; print(json.dumps({"username": sys.argv[1], "password": sys.argv[2]}))' "\${admin_email}" "\${admin_password}")" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

has_analytics_db="\$(curl -s http://localhost:3000/api/database -H "X-Metabase-Session: \${session_id}" \
  | python3 -c 'import json,sys; print(any(db.get("name") == "analytics" for db in json.load(sys.stdin).get("data", [])))')"
if [ "\$has_analytics_db" = "True" ]; then
  echo "analytics database connection already exists in metabase"
else
  echo "connecting the analytics schema to metabase"
  curl -s -X POST http://localhost:3000/api/database \
    -H 'Content-Type: application/json' -H "X-Metabase-Session: \${session_id}" \
    -d "\$(python3 -c 'import json,sys; print(json.dumps({"engine": "postgres", "name": "analytics", "details": {"host": "postgres", "port": 5432, "dbname": sys.argv[1], "user": "analytics_reader", "password": sys.argv[2], "schema-filters-type": "inclusion", "schema-filters-patterns": "analytics"}}))' "${APP_DB_NAME}" "\${analytics_reader_password}")" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); assert "id" in d, f"database connection failed: {d}"'
fi

echo '${caddyfile_with_metabase_b64}' | base64 -d >/opt/dispute-intake/infra/Caddyfile
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile
echo "metabase deployed and reachable through Caddy"
SCRIPT
)"

params_file="$(mktemp)"
trap 'rm -f "${params_file}"' EXIT
jq -n --arg script "${remote_script}" '{commands: [$script]}' >"${params_file}"

log "sending the metabase provisioning command to ${instance_id}"
command_id="$(aws ssm send-command \
  --instance-ids "${instance_id}" \
  --document-name "AWS-RunShellScript" \
  --parameters "file://${params_file}" \
  --query "Command.CommandId" --output text)"

log "waiting for the metabase provisioning command to finish"
for _ in $(seq 1 60); do
  status="$(aws ssm get-command-invocation \
    --command-id "${command_id}" --instance-id "${instance_id}" \
    --query "Status" --output text 2>/dev/null || echo "Pending")"
  case "${status}" in
    Success)
      log "metabase provisioning succeeded; output follows (carries the memory-footprint reading)"
      aws ssm get-command-invocation --command-id "${command_id}" --instance-id "${instance_id}" \
        --query "StandardOutputContent" --output text
      exit 0
      ;;
    Failed|Cancelled|TimedOut)
      log "metabase provisioning ${status}; output follows"
      aws ssm get-command-invocation --command-id "${command_id}" --instance-id "${instance_id}" \
        --query "StandardOutputContent" --output text
      aws ssm get-command-invocation --command-id "${command_id}" --instance-id "${instance_id}" \
        --query "StandardErrorContent" --output text >&2
      exit 1
      ;;
    *) sleep 10 ;;
  esac
done
log "refusing: metabase provisioning did not finish within 10 minutes"
exit 1
