#!/usr/bin/env bash
# =============================================================================
# put-secret.sh — create or update one secret under the project's SSM prefix
# =============================================================================
# Purpose:
#   The one way a secret the deployed host reads at start-up is added (the
#   model API key, the session-signing key and the demonstration access
#   codes all live this way).
#   Generic on purpose, so no script anywhere needs to hardcode a new
#   parameter name to add one.
# Design:
#   The value is read from stdin, never a command-line argument (arguments
#   can end up in shell history and process listings); it is never echoed
#   and never passed to aws inline — it goes through a mode-600 temporary
#   file that put-parameter reads via its own file:// convention, and the
#   file is removed on exit, success or failure.
#   The name is confined to this project's own path prefix, so a typo cannot
#   silently write over an unrelated parameter elsewhere in the account.
# Usage:
#   openssl rand -hex 32 | infra/scripts/put-secret.sh session-signing-key
#   (writes /transaction-disputes/prod/session-signing-key)
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

require_profile
require_live_credentials

readonly SSM_PATH_PREFIX="/transaction-disputes/prod"

if [[ $# -ne 1 ]]; then
  log "usage: put-secret.sh <name>   (the value is read from stdin)"
  exit 1
fi
readonly secret_name="$1"
if [[ ! "${secret_name}" =~ ^[a-z0-9-]+$ ]]; then
  log "refusing: name must be lowercase letters, digits and hyphens only"
  exit 1
fi

secret_value="$(cat -)"
if [[ -z "${secret_value}" ]]; then
  log "refusing: no value was piped in on stdin"
  exit 1
fi

parameter_name="${SSM_PATH_PREFIX}/${secret_name}"

# The value never reaches the aws process as a command-line argument (visible to any other
# process on the host via `ps` or /proc/<pid>/cmdline for as long as put-parameter runs): it is
# written to a private temporary file instead, and put-parameter reads it back through its own
# file:// convention. mktemp already creates the file at mode 600 regardless of the process's
# umask; the explicit chmod is belt-and-braces against a nonstandard mktemp. The trap removes the
# file on any exit path.
secret_file="$(mktemp)"
trap 'rm -f "${secret_file}"' EXIT
chmod 600 "${secret_file}"
printf '%s' "${secret_value}" >"${secret_file}"

# `put-parameter --overwrite` refuses `--tags` in the same call (SSM's own rule: tag an existing
# parameter through a separate operation); tagging is done as its own step so this script works
# unchanged whether the parameter is new or already exists.
aws ssm put-parameter \
  --name "${parameter_name}" \
  --type "SecureString" \
  --value "file://${secret_file}" \
  --overwrite \
  >/dev/null

aws ssm add-tags-to-resource \
  --resource-type "Parameter" \
  --resource-id "${parameter_name}" \
  --tags "Key=${INFRA_TAG_KEY},Value=${INFRA_TAG_VALUE}" \
  >/dev/null

log "stored (name only, never the value): ${parameter_name}"
