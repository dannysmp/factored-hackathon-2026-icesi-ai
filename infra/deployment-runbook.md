# Deployment runbook

The maintainer's procedure for standing the system up in an empty AWS account, setting the two demonstration access codes, verifying the result and reading the codes back for the release message. It complements [`README.md`](README.md), which describes what each script creates; this document orders the steps and states what to check after each.

## Handling secrets

Every command below is written so that a secret value appears only in the maintainer's own terminal, and only where a step says so.

- A value is always piped into `infra/scripts/put-secret.sh` on standard input, never typed as an argument: arguments end up in shell history and in process listings.
- Never paste a value, or the output of a command that prints one, into a chat, a pull request, an issue, a log, a screenshot or a recording. Examples in this document show the shape of a value, never a real one.
- Clear the terminal scrollback after any step that printed a value.
- The access codes gate the demonstration only. Real authentication and authorization are enforced whether or not a code is known (see `docs/limitations.md`).

## Before starting

- The AWS profile `transaction-disputes` is signed in: `aws sso login --profile transaction-disputes`. The scripts refuse any other profile or region.
- The GitHub Actions repository secret `AWS_ACCOUNT_ID` holds the target account's numeric ID.
- The operational seed is built on this machine with the data provider's own profile (`make pipeline && make seed`), so `data/gold/ops_seed/` exists. It is never built in CI or on the host.
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

- Both access codes are at least 16 characters. The pipeline above yields about 30.
- `demo-signin-access-code` differs from `demo-agent-access-code`, and `agent-session-signing-key` differs from `session-signing-key`. Generate each independently; never copy one value into two parameters.
- The customer sign-in turns on when `demo-signin-access-code` exists. The agent sign-in turns on only when both `demo-agent-access-code` and `agent-session-signing-key` exist.

Regenerating a code after a deployment is running changes nothing until the next deploy, because the host reads the parameters when the stack starts. Redeploy (step 4) after any regeneration.

## 4. Deploy

For a deployment meant to persist, turn the teardown off:

```sh
gh workflow run deploy.yml --ref main -f teardown_after=false -f deploy_metabase=false
gh run watch "$(gh run list --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

Set `deploy_metabase=true` only when the Metabase parameters listed in `README.md` exist. For a clean-account reproduction that is then removed, leave `teardown_after` at its default (on).

The run builds and scans both images, pushes them, brings the stack up over SSM, seeds the database, and runs the smoke test and the hardening check. Any failed step fails the run.

The host name is public (`<address-with-dashes>.sslip.io`). It appears in the run log where the smoke test step is echoed with it:

```sh
run_id="$(gh run list --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run view "$run_id" --log | grep -o '[0-9]\{1,3\}-[0-9]\{1,3\}-[0-9]\{1,3\}-[0-9]\{1,3\}\.sslip\.io' | head -1
```

## 5. Verify

1. **The pipeline's own checks passed**: the run finished green, including the smoke test and both hardening checks.
2. **Sign-in state, without values.** `curl -s https://<host>/v1/auth/demo-personas` answers `200` only while at least one sign-in is on, and lists each persona with its `audience`. A deployment carrying both sign-ins lists personas of audience `customer` and of audience `agent`. A `404`, or a list missing one audience, means the matching parameters from step 3 were missing when the stack started: set them and redeploy. The response contains no secret.
3. **Each code works.** The access code is read from SSM inside the command and handed to `curl` through its configuration on standard input, so it never appears in the process listing, the shell history or the output. Only the HTTP status is printed. Substitute a persona slug of each audience from the list in check 2:

   ```sh
   code="$(aws ssm get-parameter --name /transaction-disputes/prod/demo-signin-access-code --with-decryption --query Parameter.Value --output text)"
   printf 'header = "X-Demo-Access-Code: %s"\n' "$code" | curl -s -o /dev/null -w '%{http_code}\n' -K - \
     -X POST -H 'Content-Type: application/json' -d '{"persona":"<customer-slug>"}' \
     "https://<host>/v1/auth/demo-sessions"
   unset code
   ```

   Expect `201`. Repeat with `demo-agent-access-code`, an agent persona slug and `/v1/auth/demo-agent-sessions`. Do not probe with a wrong code repeatedly: wrong codes count against the caller's address and are rate-limited.
4. **The web page** at `https://<host>/` loads over a valid certificate in a browser, and a customer sign-in with the code reaches the chat.
5. Record the run in the table at the end of this document.

## 6. Read the codes for the release message

This is the only step in which a value leaves AWS. Copy it straight to the clipboard so it is never printed, paste it into the message, then clear the clipboard. On macOS:

```sh
aws ssm get-parameter --name /transaction-disputes/prod/demo-signin-access-code --with-decryption --query Parameter.Value --output text | tr -d '\n' | pbcopy
# paste into the message, then:
pbcopy </dev/null
```

Repeat for `demo-agent-access-code`. Send each code only in the release message itself; do not store it in a document, a ticket or the repository.

## 7. After the release window

Turn the sign-in off by following [Turning the demonstration sign-in off](README.md#turning-the-demonstration-sign-in-off), or remove the whole host with `infra/scripts/07-teardown.sh` (the roles, repositories and seed bucket remain).

## Run record

One row per clean-account reproduction or persisting deployment, filled in by the maintainer after step 5.

| Date | Role | Mode | Outcome |
|---|---|---|---|
| | | | |
