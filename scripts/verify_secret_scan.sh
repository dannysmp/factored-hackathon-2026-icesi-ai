#!/usr/bin/env bash
# =============================================================================
# verify_secret_scan.sh — proves the secret scanner blocks a planted fake AWS key
# =============================================================================
# Purpose:  Epic E0 acceptance: "a planted fake AWS key is blocked by the scanner".
#           The scanner is only trustworthy if it is seen failing on a real-shaped key.
# Design:   The fake key is assembled at runtime in a temp directory, so no secret-shaped
#           string is ever committed. The temp directory is always removed.
# Usage:    scripts/verify_secret_scan.sh        (requires gitleaks on PATH)
# Exit:     0 when gitleaks detects the planted key, 1 otherwise.
# =============================================================================
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

# Assemble an AWS-access-key-shaped token (AKIA + 16 upper-case alphanumerics, no "EXAMPLE").
prefix="AKIA"
suffix="IOSFODNN7ZQ4XLMP"
printf 'aws_access_key_id = "%s%s"\n' "$prefix" "$suffix" > "$workdir/planted.env"

# Control: a directory without the key must scan clean, so a failure below is the key's doing.
mkdir "$workdir/clean"
echo "nothing secret here" > "$workdir/clean/notes.txt"
if ! gitleaks dir "$workdir/clean" --config "$repo_root/.gitleaks.toml" --no-banner >/dev/null 2>&1; then
  echo "FAIL: gitleaks flagged a clean control directory" >&2
  exit 1
fi

# The scanner must exit non-zero (gitleaks uses 1) when scanning the planted key.
if gitleaks dir "$workdir/planted.env" --config "$repo_root/.gitleaks.toml" --no-banner --redact >/dev/null 2>&1; then
  echo "FAIL: gitleaks did not flag the planted AWS key" >&2
  exit 1
fi
echo "OK: gitleaks blocked the planted AWS key"
