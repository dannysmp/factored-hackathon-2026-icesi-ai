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
#           gitleaks exits 1 both when it finds a leak and when it fails outright (an
#           unreadable config, for example) — the exit code alone cannot tell the two apart,
#           so a "leak" check also requires gitleaks' own "leaks found:" line in its output;
#           without that, a broken scanner could exit 1 for the wrong reason and still read as
#           a passing self-test. A dedicated scenario plants a broken config to prove this
#           distinction actually holds, not just that the exit code is checked.
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

# Run gitleaks in one mode inside a repo, against the given config. Sets SCAN_RC (exit code)
# and SCAN_OUTPUT (combined stdout+stderr) for the caller to inspect.
run_scan() {
  local dir="$1" cfg="$2"; shift 2
  set +e
  SCAN_OUTPUT="$(cd "$dir" && gitleaks git . --config "$cfg" --no-banner --redact "$@" 2>&1)"
  SCAN_RC=$?
  set -e
}

expect_result() {
  local label="$1" want="$2"
  case "$want" in
    clean)
      if [ "$SCAN_RC" != "0" ]; then
        echo "FAIL: $label — clean control was flagged (rc=$SCAN_RC)" >&2
        echo "$SCAN_OUTPUT" >&2
        exit 1
      fi
      ;;
    leak)
      # gitleaks exits 1 for a real detection AND for a fatal error (bad config, for example);
      # only the "leaks found:" line in its own output tells the two apart.
      if [ "$SCAN_RC" != "1" ]; then
        echo "FAIL: $label — planted AWS key was not flagged (rc=$SCAN_RC, expected 1)" >&2
        echo "$SCAN_OUTPUT" >&2
        exit 1
      fi
      if ! grep -q "leaks found:" <<< "$SCAN_OUTPUT"; then
        echo "FAIL: $label — exit code was 1 but no leak was actually reported (scanner error?)" >&2
        echo "$SCAN_OUTPUT" >&2
        exit 1
      fi
      ;;
    scanner_error)
      # Prove our own "leak" check would correctly reject this as a failure, not a detection —
      # the acceptance case: a scanner error must never pass as a leak was found.
      if [ "$SCAN_RC" = "0" ]; then
        echo "FAIL: $label — expected the scanner to fail, but it exited 0" >&2
        echo "$SCAN_OUTPUT" >&2
        exit 1
      fi
      if grep -q "leaks found:" <<< "$SCAN_OUTPUT"; then
        echo "FAIL: $label — expected a scanner error, but a leak was reported instead" >&2
        echo "$SCAN_OUTPUT" >&2
        exit 1
      fi
      ;;
    *)
      echo "FAIL: $label — unknown expectation '$want'" >&2
      exit 1
      ;;
  esac
}

# ---- Committed-history mode (`gitleaks git`) ----------------------------------
clean_repo="$workdir/clean-history"
new_repo "$clean_repo"
run_scan "$clean_repo" "$config"
expect_result "git (history)" clean

leaky_repo="$workdir/leaky-history"
new_repo "$leaky_repo"
echo "$planted_line" > "$leaky_repo/planted.env"
git -C "$leaky_repo" add planted.env
git -C "$leaky_repo" -c user.name=selftest -c user.email=selftest@example.invalid \
  -c commit.gpgsign=false commit -q -m "plant fake key"
run_scan "$leaky_repo" "$config"
expect_result "git (history)" leak
echo "OK: gitleaks git (history) blocked the planted AWS key; clean control passed"

# ---- Staged-changes mode (`gitleaks git --pre-commit --staged`) ---------------
clean_staged="$workdir/clean-staged"
new_repo "$clean_staged"
echo "still nothing secret" > "$clean_staged/more.txt"
git -C "$clean_staged" add more.txt
run_scan "$clean_staged" "$config" --pre-commit --staged
expect_result "git --staged" clean

leaky_staged="$workdir/leaky-staged"
new_repo "$leaky_staged"
echo "$planted_line" > "$leaky_staged/planted.env"
git -C "$leaky_staged" add planted.env
run_scan "$leaky_staged" "$config" --pre-commit --staged
expect_result "git --staged" leak
echo "OK: gitleaks git --staged blocked the planted AWS key; clean control passed"

# ---- Scanner-error mode: a broken scanner must never be mistaken for "leak found" ----
broken_config="$workdir/broken.toml"
echo "this is not valid toml [[[" > "$broken_config"
error_repo="$workdir/error-repo"
new_repo "$error_repo"
run_scan "$error_repo" "$broken_config"
expect_result "git (broken config)" scanner_error
echo "OK: a scanner error (unreadable config) is not mistaken for a detected leak"
