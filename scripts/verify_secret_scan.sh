#!/usr/bin/env bash
# =============================================================================
# verify_secret_scan.sh — proves the secret scanner blocks a planted fake AWS key
# =============================================================================
# Purpose:  Prove that the secret scanner blocks a planted fake AWS key.
#           The scanner is only trustworthy if it is seen failing on a real-shaped key
#           in every scan mode the gates use: `gitleaks git` (committed history, run by
#           `make secrets` and CI) and `gitleaks git --pre-commit --staged` (staged changes).
# Design:   The fake key is assembled at runtime inside throwaway git repositories, so no
#           secret-shaped string is ever committed. Each mode is checked against a clean
#           control (must exit 0) and a planted repo (must exit non-zero). Temp dirs are
#           always removed.
# Usage:    scripts/verify_secret_scan.sh        (requires gitleaks and git on PATH)
# Exit:     0 when every mode behaves as expected, 1 otherwise.
# Limits:   Files that are neither staged nor committed are NOT covered by these modes;
#           see the note in the Makefile `secrets` target and the README.
# =============================================================================
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="$repo_root/.gitleaks.toml"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

# Assemble an AWS-access-key-shaped token (AKIA + 16 upper-case alphanumerics, no "EXAMPLE").
planted_line="$(printf 'aws_access_key_id = "%s%s"' "AKIA" "IOSFODNN7ZQ4XLMP")"

# Create a throwaway git repository with one harmless committed file.
new_repo() {
  local dir="$1"
  git init -q "$dir"
  echo "nothing secret here" > "$dir/notes.txt"
  git -C "$dir" add notes.txt
  git -C "$dir" -c user.name=selftest -c user.email=selftest@example.invalid \
    -c commit.gpgsign=false commit -q -m "clean baseline"
}

# Run gitleaks in one mode inside a repo; echo its exit code (0 = no leak found, 1 = leak).
scan_rc() {
  local dir="$1"; shift
  local rc=0
  (cd "$dir" && gitleaks git . --config "$config" --no-banner --redact "$@" >/dev/null 2>&1) || rc=$?
  echo "$rc"
}

expect_rc() {
  local label="$1" want="$2" got="$3"
  if [ "$want" = "clean" ] && [ "$got" != "0" ]; then
    echo "FAIL: $label — clean control was flagged (rc=$got)" >&2; exit 1
  fi
  # gitleaks exits 1 when it finds a leak; any other non-zero code is a crash, not a detection.
  if [ "$want" = "leak" ] && [ "$got" != "1" ]; then
    echo "FAIL: $label — planted AWS key was not flagged (rc=$got, expected 1)" >&2; exit 1
  fi
}

# ---- Committed-history mode (`gitleaks git`) ----------------------------------
clean_repo="$workdir/clean-history"
new_repo "$clean_repo"
expect_rc "git (history)" clean "$(scan_rc "$clean_repo")"

leaky_repo="$workdir/leaky-history"
new_repo "$leaky_repo"
echo "$planted_line" > "$leaky_repo/planted.env"
git -C "$leaky_repo" add planted.env
git -C "$leaky_repo" -c user.name=selftest -c user.email=selftest@example.invalid \
  -c commit.gpgsign=false commit -q -m "plant fake key"
expect_rc "git (history)" leak "$(scan_rc "$leaky_repo")"
echo "OK: gitleaks git (history) blocked the planted AWS key; clean control passed"

# ---- Staged-changes mode (`gitleaks git --pre-commit --staged`) ---------------
clean_staged="$workdir/clean-staged"
new_repo "$clean_staged"
echo "still nothing secret" > "$clean_staged/more.txt"
git -C "$clean_staged" add more.txt
expect_rc "git --staged" clean "$(scan_rc "$clean_staged" --pre-commit --staged)"

leaky_staged="$workdir/leaky-staged"
new_repo "$leaky_staged"
echo "$planted_line" > "$leaky_staged/planted.env"
git -C "$leaky_staged" add planted.env
expect_rc "git --staged" leak "$(scan_rc "$leaky_staged" --pre-commit --staged)"
echo "OK: gitleaks git --staged blocked the planted AWS key; clean control passed"
