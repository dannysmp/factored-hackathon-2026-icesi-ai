#!/usr/bin/env bash
# =============================================================================
# 06-smoke-test.sh — prove the deployed path actually answers over HTTPS
# =============================================================================
# Purpose:
#   Exercises the whole path the deploy just brought up: Caddy's automatic
#   certificate for the sslip.io host name, the reverse proxy routing to the
#   backend and to the web build (streams.md 3.12a: "the health endpoint, the
#   web static page and the database"). Tests the deployment path itself, not
#   functional completeness.
# Design:
#   Certificate issuance and the containers' own start-up both take a few
#   seconds after `docker compose up -d` returns; retries with a fixed
#   backoff rather than treating the first failure as final.
# Usage:
#   infra/scripts/06-smoke-test.sh <host-name>
#   (the sslip.io host name 05-deploy.sh printed on its last line)
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

if [[ $# -ne 1 ]]; then
  log "usage: 06-smoke-test.sh <host-name>"
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

check "/health/live" '"live"'
check "/" "<"

log "smoke test passed: ${BASE_URL}"
