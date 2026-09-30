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
  # The value of one response header from a real GET request, or empty if absent. A header
  # simply not being present is an expected, valid outcome here, not a script error: piping
  # through grep, which exits non-zero on no match, must not trip "set -o pipefail" into
  # aborting the whole check over what a caller like check_csp_enforcing needs to see as an
  # empty result.
  local path="$1" header_name="$2"
  curl --silent --max-time 10 -D - -o /dev/null "${BASE_URL}${path}" 2>/dev/null \
    | { grep -i "^${header_name}:" || true; } | tr -d '\r' | sed -E "s/^[^:]+:[[:space:]]*//" | head -1
}

post_response_header() {
  # The value of one response header from a real POST request with a JSON body and a custom
  # header, or empty if absent. Used for the two sign-in routes, the only requests in this
  # deployment that could ever set a session cookie -- a GET against an unrelated route (the
  # original design here) can never observe a cookie a sign-in handler sets, whatever the
  # handler does, since it never runs that code path at all.
  local path="$1" header_name="$2" body="$3" access_code_header="$4"
  curl --silent --max-time 10 -D - -o /dev/null -X POST \
    -H "Content-Type: application/json" -H "${access_code_header}" \
    -d "${body}" "${BASE_URL}${path}" 2>/dev/null \
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
  # A report-only CSP alone never satisfies this: it is read from a header this deployment
  # never sends at all (the enforcing one, checked below, is the only Content-Security-Policy
  # this Caddyfile sets), so its presence or absence cannot itself change the verdict here --
  # what matters is only whether the enforcing header exists.
  log "checking Content-Security-Policy is enforcing, not report-only..."
  local enforcing
  enforcing="$(response_header /health/live content-security-policy)"
  if [[ -z "${enforcing}" ]]; then
    log "FAIL: Content-Security-Policy is missing (a report-only policy alone would not satisfy this either)"
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
  # Probes the two routes that could ever set a session cookie -- the customer and agent demo
  # sign-in brokers (ADR-18) -- not an unrelated route. A wrong access code still reaches each
  # handler's real code (the code is compared before anything else, including the persona
  # lookup), so this needs no real credentials to be a genuine, non-vacuous test of what a
  # refused sign-in attempt actually does. Known residual gap: a cookie set only on a
  # *successful* sign-in (a 201, which this script cannot reach without the real access code)
  # is outside what this check can observe; it catches a cookie set unconditionally or on
  # refusal, which is the realistic shape most such a regression would take, not every
  # conceivable one.
  log "checking session cookie flags on the sign-in routes (skipped when no cookie is ever set)..."
  local path persona set_cookie any_cookie=0 own_fail=0
  # A real, currently-committed persona slug for each route (not a made-up placeholder): an
  # unknown slug could be refused by validation before the handler's own code ever runs, which
  # would make this check pass for the wrong reason. A wrong access code still reaches each
  # handler's real logic either way -- the code is compared first, before the persona lookup.
  for path_and_persona in "/v1/auth/demo-sessions ana" "/v1/auth/demo-agent-sessions agent-beatriz"; do
    path="${path_and_persona%% *}"
    persona="${path_and_persona#* }"
    set_cookie="$(post_response_header "${path}" set-cookie "{\"persona\":\"${persona}\"}" \
      "X-Demo-Access-Code: __hardening_check_wrong_code__")"
    if [[ -z "${set_cookie}" ]]; then
      continue
    fi
    any_cookie=1
    if grep -qi "secure" <<<"${set_cookie}" \
      && grep -qi "httponly" <<<"${set_cookie}" \
      && grep -qiE "samesite=(strict|lax)" <<<"${set_cookie}"; then
      log "ok: the cookie ${path} sets carries Secure, HttpOnly and SameSite=Strict|Lax"
    else
      log "FAIL: the cookie ${path} sets is missing Secure, HttpOnly or SameSite=Strict|Lax: ${set_cookie}"
      fail=1
      own_fail=1
    fi
  done
  if [[ "${any_cookie}" -eq 0 ]]; then
    log "skipped: no session cookie is set anywhere in this deployment (bearer-token sessions)"
  elif [[ "${own_fail}" -eq 0 ]]; then
    log "ok: every session cookie set by a sign-in route carries Secure, HttpOnly and SameSite=Strict|Lax"
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
