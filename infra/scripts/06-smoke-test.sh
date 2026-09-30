#!/usr/bin/env bash
# =============================================================================
# 06-smoke-test.sh — prove the deployed path actually answers over HTTPS
# =============================================================================
# Purpose:
#   Exercises the whole path the deploy just brought up: Caddy's automatic
#   certificate for the sslip.io host name, and the reverse proxy routing to
#   the backend's health endpoint and to the web build's static page. Tests
#   the deployment path itself, not functional completeness. The optional
#   `--dashboard` flag additionally proves the `dashboard.` subdomain reaches
#   Metabase, once `08-deploy-metabase.sh` has run.
# Design:
#   Certificate issuance and the containers' own start-up both take a few
#   seconds after `docker compose up -d` returns; retries with a fixed
#   backoff rather than treating the first failure as final. The dashboard
#   check only proves the path is reachable (HTTP success), not a specific
#   response body: unlike the app's own endpoints, Metabase's health payload
#   isn't a contract this repository owns.
# Usage:
#   infra/scripts/06-smoke-test.sh <host-name> [--dashboard]
#   (the sslip.io host name 05-deploy.sh printed on its last line)
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

if [[ $# -lt 1 || $# -gt 2 || ( $# -eq 2 && "$2" != "--dashboard" ) ]]; then
  log "usage: 06-smoke-test.sh <host-name> [--dashboard]"
  exit 1
fi
readonly HOST_NAME="$1"
readonly BASE_URL="https://${HOST_NAME}"
readonly MAX_ATTEMPTS=12
readonly RETRY_SECONDS=10

check() {
  local path="$1" expect_substring="$2" attempt=1 body
  while (( attempt <= MAX_ATTEMPTS )); do
    if body="$(curl --fail --silent --show-error --max-time 10 "${BASE_URL}${path}" 2>&1)"; then
      if [[ "${body}" == *"${expect_substring}"* ]]; then
        log "ok: ${path}"
        return 0
      fi
      log "attempt ${attempt}: ${path} answered but did not contain '${expect_substring}': ${body}"
    else
      log "attempt ${attempt}: ${path} not reachable yet: ${body}"
    fi
    attempt=$((attempt + 1))
    sleep "${RETRY_SECONDS}"
  done
  log "refusing: ${path} never answered as expected within $((MAX_ATTEMPTS * RETRY_SECONDS))s"
  return 1
}

check_reachable() {
  local url="$1" attempt=1
  while (( attempt <= MAX_ATTEMPTS )); do
    if curl --fail --silent --show-error --max-time 10 -o /dev/null "${url}"; then
      log "ok: ${url}"
      return 0
    fi
    log "attempt ${attempt}: ${url} not reachable yet"
    attempt=$((attempt + 1))
    sleep "${RETRY_SECONDS}"
  done
  log "refusing: ${url} never became reachable within $((MAX_ATTEMPTS * RETRY_SECONDS))s"
  return 1
}

# Proves the proxy's own 64 KiB request-body limit (infra/Caddyfile) actually rejects an
# oversized body with 413, before it ever reaches the backend -- not just that the Caddyfile
# declares one.
check_body_size_limit() {
  local url="$1" attempt=1 status oversized_body
  oversized_body="$(head -c 70000 /dev/zero | tr '\0' 'a')"
  while (( attempt <= MAX_ATTEMPTS )); do
    status="$(curl --silent --show-error --max-time 10 -o /dev/null -w '%{http_code}' \
      -X POST -H "Content-Type: application/json" --data "${oversized_body}" "${url}" 2>/dev/null || echo "000")"
    if [[ "${status}" == "413" ]]; then
      log "ok: ${url} rejected a 70000-byte body with 413"
      return 0
    fi
    log "attempt ${attempt}: ${url} returned ${status} for an oversized body, expected 413"
    attempt=$((attempt + 1))
    sleep "${RETRY_SECONDS}"
  done
  log "refusing: ${url} never rejected an oversized body with 413 within $((MAX_ATTEMPTS * RETRY_SECONDS))s"
  return 1
}

check "/health/live" '"live"'
check "/" "<"
check_body_size_limit "${BASE_URL}/health/live"

if [[ "${2:-}" == "--dashboard" ]]; then
  check_reachable "https://dashboard.${HOST_NAME}/api/health"
fi

log "smoke test passed: ${BASE_URL}"
