# Deployment runbook

The maintainer's procedure for standing the system up in an empty AWS account, setting the two demonstration access codes, verifying the result and reading the codes back for the release message. It complements [`README.md`](README.md), which describes what each script creates; this document orders the steps and states what to check after each.

## Handling secrets

Every command below is written so that a secret value appears only in the maintainer's own terminal, and only where a step says so.

- A value is always piped into `infra/scripts/put-secret.sh` on standard input, never typed as an argument: arguments end up in shell history and in process listings.
- Never paste a value, or the output of a command that prints one, into a chat, a pull request, an issue, a log, a screenshot or a recording. Examples in this document show the shape of a value, never a real one.
- Clear the terminal scrollback after any step that printed a value.
- The access codes gate the demonstration only. Real authentication and authorization are enforced whether or not a code is known (see `docs/limitations.md`).

## Before starting

- The AWS profile `transaction-disputes` is signed in: `aws sso login --profile transaction-disputes`. The scripts refuse any other profile or region, so the shell must not hold a different `AWS_PROFILE` or `AWS_REGION`. Every `aws` command below also relies on these, so set them once in the shell that runs this document:

  ```sh
  export AWS_PROFILE=transaction-disputes AWS_REGION=us-east-1
  ```

- The GitHub CLI is signed in to this repository (`gh auth status`): script `01` and the deploy commands use it.
- The GitHub Actions repository secret `AWS_ACCOUNT_ID` holds the target account's numeric ID.
- The operational seed is built on this machine from the raw data already in `data/raw` (`make pipeline && make seed`, which need no AWS profile), so `data/gold/ops_seed/` exists. It is never built in CI or on the host.
- The repository is on `main`, up to date, and CI is green on the commit that will be deployed.

## 1. Provision the account

Run in this order from the repository root; each script is idempotent.

```sh
infra/scripts/01-create-oidc-role.sh
infra/scripts/02-create-ecr-repos.sh
infra/scripts/03-create-instance-role.sh
infra/scripts/04-launch-instance.sh
infra/scripts/11-create-seed-bucket.sh
aws s3 sync data/gold/ops_seed/ "s3://$(infra/scripts/11-create-seed-bucket.sh)/ops_seed/"
```

Check: `aws s3 ls "s3://$(infra/scripts/11-create-seed-bucket.sh)/ops_seed/"` lists the seed files and their manifest.

## 2. Set the required secrets

The deploy fails before bringing the stack up if any of these is missing.

```sh
# The model API key: read it without echo, pipe it, then discard it.
read -rs ANTHROPIC_KEY && printf '%s' "$ANTHROPIC_KEY" | infra/scripts/put-secret.sh anthropic-api-key; unset ANTHROPIC_KEY

openssl rand -hex 32 | infra/scripts/put-secret.sh session-signing-key
openssl rand -hex 32 | infra/scripts/put-secret.sh postgres-password
```

Check: `aws ssm describe-parameters --parameter-filters "Key=Path,Values=/transaction-disputes/prod"` lists the three names. It never returns a value.

## 3. Generate the two demonstration access codes and the agent signing key

Each value is generated and stored in one pipeline, so it is never displayed. Rerunning the same command regenerates the value: `put-secret.sh` overwrites.

```sh
openssl rand -base64 24 | tr -d '/+=' | infra/scripts/put-secret.sh demo-signin-access-code
openssl rand -base64 24 | tr -d '/+=' | infra/scripts/put-secret.sh demo-agent-access-code
openssl rand -hex 32                   | infra/scripts/put-secret.sh agent-session-signing-key
```

Constraints the backend enforces at start-up, so a mistake here fails the deployment rather than weakening it:

- Both access codes are at least 16 characters. The pipeline above yields about 30. `agent-session-signing-key` is at least 32 characters; the 64 hexadecimal characters above satisfy it.
- `demo-signin-access-code` differs from `demo-agent-access-code`, and `agent-session-signing-key` differs from `session-signing-key`. Generate each independently; never copy one value into two parameters.
- The customer sign-in turns on when `demo-signin-access-code` exists. The agent sign-in turns on only when both `demo-agent-access-code` and `agent-session-signing-key` exist.

Regenerating a code after a deployment is running changes nothing until the next deploy, because the host reads the parameters when the stack starts. Redeploy (step 4) after any regeneration.

## 4. Deploy

For a deployment meant to persist, turn the teardown off:

```sh
gh workflow run deploy.yml --ref main -f teardown_after=false -f deploy_metabase=false
sleep 10
gh run list --workflow deploy.yml --limit 3
```

Confirm the newest row is the run just started (a run listed immediately after dispatch can still be the previous one), then follow it; `--exit-status` makes the command fail when the run fails:

```sh
run_id="$(gh run list --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$run_id" --exit-status
```

Set `deploy_metabase=true` only when the Metabase parameters listed in `README.md` exist. For a clean-account reproduction that is then removed, leave `teardown_after` at its default (on).

The run builds and scans both images, pushes them, brings the stack up over SSM, seeds the database, and runs the smoke test and the hardening check. Any failed step fails the run.

The host name is public (`<address-with-dashes>.sslip.io`). It appears in the run log where the smoke test step is echoed with it (same `run_id` as above):

```sh
gh run view "$run_id" --log | grep -o '[0-9]\{1,3\}-[0-9]\{1,3\}-[0-9]\{1,3\}-[0-9]\{1,3\}\.sslip\.io' | head -1
```

## 5. Verify

Which checks apply depends on the mode. With `teardown_after` left on, the host no longer exists once the run ends, so the only evidence is the green run: check 1. Checks 2 to 4 need a deployment that persists (`teardown_after=false`).

1. **The pipeline's own checks passed**: the run finished green, including the smoke test and the hardening check. When `deploy_metabase` was on, the run also includes the dashboard smoke test and a second hardening check.
2. **Sign-in state, without values.** The first command prints the response body and then the HTTP status:

   ```sh
   curl -s -w '\n%{http_code}\n' "https://<host>/v1/auth/demo-personas"
   ```

   It answers `200` only while at least one sign-in is on, and lists each persona with its `audience`. A deployment carrying both sign-ins lists personas of audience `customer` and of audience `agent`. With both sign-ins off the backend answers `401` (`session_missing`), and a list missing one audience means that sign-in's parameters from step 3 were missing when the stack started: set them and redeploy. The response contains no secret.
3. **Each code works.** The access code is read from SSM inside the command and handed to `curl` through its configuration on standard input, so it never appears in the process listing, the shell history or the output. The command sends nothing unless the read returned a value, and prints only the HTTP status. Substitute a persona slug from the list in check 2:

   ```sh
   code="$(aws ssm get-parameter --name /transaction-disputes/prod/demo-signin-access-code --with-decryption --query Parameter.Value --output text)" \
     && [ -n "$code" ] \
     && printf 'header = "X-Demo-Access-Code: %s"\n' "$code" | curl -s -o /dev/null -w '%{http_code}\n' -K - \
       -X POST -H 'Content-Type: application/json' -d '{"persona":"<customer-slug>"}' \
       "https://<host>/v1/auth/demo-sessions"
   unset code
   ```

   Expect `201`. Repeat with `demo-agent-access-code`, an agent persona slug and `/v1/auth/demo-agent-sessions`. Each success issues a real session that holds that persona (one session per persona, for 30 minutes for a customer and 60 for an agent), so use a different persona for each of the two codes and for any later manual sign-in, or the second call answers `429`. Do not probe with a wrong code repeatedly: wrong codes count against the caller's address and are rate-limited.
4. **The web page** at `https://<host>/` loads over a valid certificate in a browser, and a customer sign-in with the code reaches the chat (with a persona not already used in check 3).
5. Record the run in the table at the end of this document.

## 6. Read the codes for the release message

This is the only step in which a value leaves AWS. Copy it straight to the clipboard so it is never printed, paste it into the message, then clear the clipboard. The subshell fails loudly if the read fails, instead of leaving an empty clipboard. On macOS:

```sh
( set -o pipefail; aws ssm get-parameter --name /transaction-disputes/prod/demo-signin-access-code --with-decryption --query Parameter.Value --output text | tr -d '\n' | pbcopy ) || echo "read failed: nothing was copied"
# paste into the message, then:
pbcopy </dev/null
```

The clipboard is not private: a clipboard manager may keep its history, and Universal Clipboard can copy the value to other devices signed in to the same account. Quit any clipboard manager and turn Handoff off for the duration, or clear the clipboard immediately after pasting.

Repeat for `demo-agent-access-code`. Send each code only in the release message itself; do not store it in a document, a ticket or the repository.

## 7. After the release window

Turn the sign-in off by following [Turning the demonstration sign-in off](README.md#turning-the-demonstration-sign-in-off), or remove the whole host with `infra/scripts/07-teardown.sh` (the roles, repositories and seed bucket remain).

## Run record

One row per clean-account reproduction or persisting deployment, filled in by the maintainer after step 5.

| Date | Role | Mode | Outcome |
|---|---|---|---|
| | | | |
