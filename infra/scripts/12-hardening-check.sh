#!/usr/bin/env bash
# =============================================================================
# 12-hardening-check.sh — the post-deploy hardening check (ADR-13)
# =============================================================================
# Purpose:
#   Proves the deployed edge actually carries the hardening baseline
#   ADR-13 requires, against the live address, after the smoke test and
#   before any account-touching run reaches it: TLS 1.1 and below refused
#   while a plain HTTPS request succeeds with a valid certificate chain; an
#   HSTS header with a max-age of at least 15,552,000 seconds; an enforcing
#   Content-Security-Policy (never report-only alone); no version token on
#   the Server or X-Powered-By headers; and, wherever a session cookie is
#   set, Secure, HttpOnly and SameSite=Strict or Lax — skipped, not failed,
#   when the deployment sets no session cookie at all (this one never does:
#   sessions are bearer tokens, confirmed by grepping the whole `app/`
#   package for `set_cookie`/`Set-Cookie` — zero hits).
# Design:
#   Every check probes the live host over the real network, the same path
#   a real client takes; nothing here reads `infra/Caddyfile` or asserts
#   configuration, only the actual response a request gets. `--tls-max 1.1`
#   (not `openssl s_client`, which had a tooling-specific handshake quirk
#   against Caddy's HTTP/2 in local testing unrelated to the real protocol
#   behavior) is what a real legacy client attempting a weak handshake
#   would present; a valid certificate chain check omits `-k` so curl's own
#   trust store does the verification a real browser would do.
# Usage:
#   infra/scripts/12-hardening-check.sh <host-name>
#   (the sslip.io host name 05-deploy.sh printed on its last line)
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

if [[ $# -ne 1 ]]; then
  log "usage: 12-hardening-check.sh <host-name>"
  exit 1
fi
readonly HOST_NAME="$1"
readonly BASE_URL="https://${HOST_NAME}"

fail=0

response_header() {
  # The value of one response header from a real request, or empty if absent. A header simply
  # not being present is an expected, valid outcome here, not a script error: piping through
  # grep, which exits non-zero on no match, must not trip "set -o pipefail" into aborting the
  # whole check over what a caller like check_csp_enforcing needs to see as an empty result.
  local path="$1" header_name="$2"
  curl --silent --max-time 10 -D - -o /dev/null "${BASE_URL}${path}" 2>/dev/null \
    | { grep -i "^${header_name}:" || true; } | tr -d '\r' | sed -E "s/^[^:]+:[[:space:]]*//" | head -1
}

check_tls_below_1_2_refused() {
  log "checking TLS 1.1 and below are refused..."
  if curl --silent --max-time 10 --tls-max 1.1 "${BASE_URL}/health/live" -o /dev/null 2>/dev/null; then
    log "FAIL: a TLS 1.1 handshake was accepted; expected refusal"
    fail=1
  else
    log "ok: TLS 1.1 and below refused"
  fi
}

check_valid_https() {
  log "checking a plain HTTPS request succeeds with a valid certificate chain..."
  if curl --fail --silent --max-time 10 "${BASE_URL}/health/live" -o /dev/null; then
    log "ok: HTTPS succeeds with a certificate chain curl's own trust store accepts"
  else
    log "FAIL: the HTTPS request failed (bad certificate chain, or the host is unreachable)"
    fail=1
  fi
}

check_hsts() {
  log "checking Strict-Transport-Security..."
  local value max_age
  value="$(response_header /health/live strict-transport-security)"
  if [[ -z "${value}" ]]; then
    log "FAIL: Strict-Transport-Security header is missing"
    fail=1
    return
  fi
  max_age="$(grep -oE 'max-age=[0-9]+' <<<"${value}" | grep -oE '[0-9]+' || true)"
  if [[ -z "${max_age}" ]] || (( max_age < 15552000 )); then
    log "FAIL: Strict-Transport-Security max-age is ${max_age:-absent}, need >= 15552000"
    fail=1
  else
    log "ok: Strict-Transport-Security present, max-age=${max_age}"
  fi
}

check_csp_enforcing() {
  log "checking Content-Security-Policy is enforcing, not report-only..."
  local enforcing report_only
  enforcing="$(response_header /health/live content-security-policy)"
  report_only="$(response_header /health/live content-security-policy-report-only)"
  if [[ -z "${enforcing}" ]]; then
    log "FAIL: Content-Security-Policy header is missing"
    fail=1
  elif [[ -n "${report_only}" && -z "${enforcing}" ]]; then
    log "FAIL: only a report-only Content-Security-Policy is set"
    fail=1
  else
    log "ok: Content-Security-Policy present and enforcing"
  fi
}

check_no_version_disclosure() {
  # Checks both routes Caddy fronts, not just one: the backend (uvicorn) and the static web
  # build (nginx) are different upstreams with independent default Server headers, and nginx's
  # own default carries a real version (nginx/x.y.z) where uvicorn's bare default does not — a
  # check against only one route would never have caught the original nginx leak.
  log "checking Server and X-Powered-By carry no version token on every route..."
  local path server powered_by own_fail=0
  for path in /health/live /; do
    server="$(response_header "${path}" server)"
    powered_by="$(response_header "${path}" x-powered-by)"
    if [[ -n "${powered_by}" ]]; then
      log "FAIL: X-Powered-By is present on ${path}: ${powered_by}"
      fail=1
      own_fail=1
    fi
    if grep -qE '[0-9]+\.[0-9]+' <<<"${server}"; then
      log "FAIL: Server header on ${path} carries a version token: ${server}"
      fail=1
      own_fail=1
    fi
  done
  if [[ "${own_fail}" -eq 0 ]]; then
    log "ok: no version token on Server or X-Powered-By, on any route"
  fi
}

check_cookie_flags() {
  log "checking session cookie flags (skipped when no cookie is ever set)..."
  local set_cookie
  set_cookie="$(response_header /health/live set-cookie)"
  if [[ -z "${set_cookie}" ]]; then
    log "skipped: no session cookie is set anywhere in this deployment (bearer-token sessions)"
    return
  fi
  if grep -qi "secure" <<<"${set_cookie}" \
    && grep -qi "httponly" <<<"${set_cookie}" \
    && grep -qiE "samesite=(strict|lax)" <<<"${set_cookie}"; then
    log "ok: session cookie carries Secure, HttpOnly and SameSite=Strict|Lax"
  else
    log "FAIL: session cookie is missing Secure, HttpOnly or SameSite=Strict|Lax: ${set_cookie}"
    fail=1
  fi
}

check_tls_below_1_2_refused
check_valid_https
check_hsts
check_csp_enforcing
check_no_version_disclosure
check_cookie_flags

if [[ "${fail}" -ne 0 ]]; then
  log "refusing: one or more hardening checks failed"
  exit 1
fi
log "hardening check passed: ${BASE_URL}"
