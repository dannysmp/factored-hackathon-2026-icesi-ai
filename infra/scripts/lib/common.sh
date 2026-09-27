#!/usr/bin/env bash
# =============================================================================
# lib/common.sh — shared guards and helpers for every infra script
# =============================================================================
# Purpose:
#   One place for the profile/region guard every script starts with, and small
#   idempotency and logging helpers, so no script invents its own copy.
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
# =============================================================================

set -euo pipefail

# The only profile and region every script in this directory is allowed to touch. A script that
# read a different profile from the environment could silently act against the wrong account;
# refusing here is cheaper than a wrong resource in someone else's account.
readonly INFRA_PROFILE="transaction-disputes"
readonly INFRA_REGION="us-east-1"

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" >&2
}

# Refuses to run unless AWS_PROFILE is unset or already the one profile these scripts use, and
# a caller cannot override the region to something else either. Every script sources this before
# its first AWS CLI call.
require_profile() {
  if [[ -n "${AWS_PROFILE:-}" && "${AWS_PROFILE}" != "${INFRA_PROFILE}" ]]; then
    log "refusing: AWS_PROFILE is '${AWS_PROFILE}', not '${INFRA_PROFILE}'"
    exit 1
  fi
  if [[ -n "${AWS_REGION:-}" && "${AWS_REGION}" != "${INFRA_REGION}" ]]; then
    log "refusing: AWS_REGION is '${AWS_REGION}', not '${INFRA_REGION}'"
    exit 1
  fi
  export AWS_PROFILE="${INFRA_PROFILE}"
  export AWS_REGION="${INFRA_REGION}"
}

# Confirms the profile's credentials are live before a script does anything else, with the exact
# actionable message the maintainer's own rule asks for on expiry — never refreshed here.
require_live_credentials() {
  if ! aws sts get-caller-identity >/dev/null 2>&1; then
    log "credentials for '${INFRA_PROFILE}' are missing or expired"
    log "run: aws sso login --profile ${INFRA_PROFILE}"
    exit 1
  fi
}

# Tags every resource these scripts create with the same project tag, so a console listing or a
# teardown script can find them all without guessing by name. Used by every script that sources
# this file, which is why shellcheck (checking this file alone) cannot see a use of either.
# shellcheck disable=SC2034
readonly INFRA_TAG_KEY="Project"
# shellcheck disable=SC2034
readonly INFRA_TAG_VALUE="dispute-intake"
