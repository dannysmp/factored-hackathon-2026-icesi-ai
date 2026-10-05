# Secret scan of the repository history

A scan of every commit reachable from `main`, run with the repository's own configuration and
with matched values redacted. The report names no secret and contains none.

| | |
|---|---|
| Commit scanned | `b4e85172a516f7f720e351ee2b24618ddaa1f4f5` (the tip of `main`, committed 2026-10-05 07:56 -05:00) |
| Scanner | gitleaks 8.30.1, the version the secret-scan job pins |
| Configuration | `.gitleaks.toml` at that commit |
| Command | `gitleaks git . --config .gitleaks.toml --no-banner --redact --log-opts="origin/main"` |
| History read | 429 commits, about 6.14 MB |
| Result | No leaks found; the JSON report is an empty list |
| Scanner self-test | `scripts/verify_secret_scan.sh` passed: the history scan and the staged scan each blocked a planted AWS key, a clean control passed, and an unreadable configuration is reported as an error, not as a clean scan |

`main` holds 431 commits that are not merges and 16 merges. The scanner reports 429 commits; it
reads the patch each commit introduces, and the two-commit difference from git's count has not
been reconciled.

## What the scan covers

- Every file added or changed in any commit of `main`, in every directory, including documents
  and test fixtures.
- Only history reachable from `main`. Other branches, closed pull requests and stashes are not
  read. The secret-scan job in CI checks out the full history and scans the checked-out ref on every
  change.
- The rules in `.gitleaks.toml`, which extend the scanner's default rules.

## What it does not show

- It does not show that no secret exists in a form the rules do not recognise. It is a pattern
  scan, not a proof.
- It is a scan of this commit, not of the release commit. The scan is run again, and this report
  replaced, when the release commit is fixed.
- A person has not read the last diff. That check stays open on the release checklist.

## Reproducing it

```
git fetch origin main
gitleaks git . --config .gitleaks.toml --no-banner --redact --log-opts="origin/main"
bash scripts/verify_secret_scan.sh
```

`make secrets` runs the same scan over the checked-out history, then the staged-change scan and
the self-test.
