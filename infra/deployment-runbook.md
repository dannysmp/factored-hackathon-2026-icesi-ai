# Deployment runbook

The maintainer's procedure for standing the system up in an empty AWS account, setting the two demonstration access codes, verifying the result and reading the codes back for the release message. It complements [`README.md`](README.md), which describes what each script creates; this document orders the steps and states what to check after each.

## Handling secrets

Every command below is written so that a secret value appears only on the maintainer's own machine (terminal, clipboard or the sign-in form), and only where a step says so.

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
- The operational seed and the dispute-demand marts are built on this machine from the raw data already in `data/raw` (`make pipeline && make seed && make analyze`, which need no AWS profile), so `data/gold/ops_seed/` and `data/gold/dispute_demand/` exist. They are never built in CI or on the host. The seed bucket must hold these same two directories: step 4 publishes them and checks them.
- The repository is on `main`, up to date, and CI is green on the commit that will be deployed.
- The monthly cost alert and the model provider's spend limit are set. Neither is created by any script or visible in the repository; see [Cost controls outside the repository](#cost-controls-outside-the-repository).

## 1. Provision the account

Run in this order from the repository root; each script is idempotent.

```sh
infra/scripts/01-create-oidc-role.sh
infra/scripts/02-create-ecr-repos.sh
infra/scripts/03-create-instance-role.sh
infra/scripts/04-launch-instance.sh
infra/scripts/11-create-seed-bucket.sh
aws s3 sync data/gold/ops_seed/ "s3://$(infra/scripts/11-create-seed-bucket.sh)/ops_seed/"
aws s3 sync --delete data/gold/dispute_demand/ "s3://$(infra/scripts/11-create-seed-bucket.sh)/dispute_demand/"
```

Check: `aws s3 ls "s3://$(infra/scripts/11-create-seed-bucket.sh)/ops_seed/"` lists the seed files and their manifest, and the same listing under `dispute_demand/` lists the marts and theirs.

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

Each value is generated and stored in one pipeline, so it is never displayed. Rerunning the same command regenerates the value: `put-secret.sh` overwrites. Once the codes have gone out in the release message they stay fixed for the evaluation period; regenerate one only if it has leaked.

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

## 4. Publish the seed, then deploy

Every deploy reloads the database from the seed bucket, so the bucket must hold the current seed before each dispatch: the operational seed (`ops_seed/`) and the dispute-demand marts (`dispute_demand/`) that feed the Operations dashboard. Refresh both whenever their schema or inputs changed since the last publish, and confirm each bucket manifest matches the local one (each prints a difference, or `seed bucket matches the local seed` and `marts bucket matches the local marts`):

```sh
make pipeline && make seed && make analyze
aws s3 sync data/gold/ops_seed/ "s3://$(infra/scripts/11-create-seed-bucket.sh)/ops_seed/" \
  && aws s3 cp "s3://$(infra/scripts/11-create-seed-bucket.sh)/ops_seed/manifest.json" - | diff - data/gold/ops_seed/manifest.json \
  && echo "seed bucket matches the local seed"
aws s3 sync --delete data/gold/dispute_demand/ "s3://$(infra/scripts/11-create-seed-bucket.sh)/dispute_demand/" \
  && aws s3 cp "s3://$(infra/scripts/11-create-seed-bucket.sh)/dispute_demand/manifest.json" - | diff - data/gold/dispute_demand/manifest.json \
  && echo "marts bucket matches the local marts"
```

A bucket built before a column existed leaves that column at its default in the deployed database, so a rule that depends on it never fires: without `customers.is_repeat_complainer`, repeat complainers are not handed to a person. The loader refuses a seed that lacks that column and names it in the error, but it guards only that column. The manifest comparison confirms the publish took effect, so the bucket holds exactly the seed just built.

For a deployment meant to persist, turn the teardown off:

```sh
gh workflow run deploy.yml --ref main -f teardown_after=false -f deploy_metabase=false
sleep 10
gh run list --workflow deploy.yml --limit 3
```

Confirm the newest row is the run just started (a run listed immediately after dispatch can still be the previous one; if it is, wait a few seconds and list again), then follow it; `--exit-status` makes the command fail when the run fails:

```sh
run_id="$(gh run list --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$run_id" --exit-status
```

Set `deploy_metabase=true` only when the Metabase parameters listed in `README.md` exist. For a clean-account reproduction that is then removed, leave `teardown_after` at its default (on).

The run builds and scans both images, pushes them, deploys over SSM (the database is migrated, seeded and loaded with the dispute-demand marts before the rest of the stack is brought up), and runs the smoke test, the hardening check and the analytics check, which fails when any table an Operations dashboard question reads holds no rows (`infra/scripts/13-verify-analytics.sh`). With `deploy_metabase=true` the dashboard job also removes Metabase's built-in sample content (its sample database and the "E-commerce Insights" example dashboard) and runs every dashboard question, failing when one returns no rows. Metabase adds its sample database back whenever it restarts, so a restart brings that one entry back until the next run with `deploy_metabase=true`; the example collections and the "E-commerce Insights" dashboard stay removed. Any failed step fails the run.

The host name is public (`<address-with-dashes>.sslip.io`). It appears in the run log where the smoke test step is echoed with it (same `run_id` as above):

```sh
gh run view "$run_id" --log | grep -o '[0-9]\{1,3\}-[0-9]\{1,3\}-[0-9]\{1,3\}-[0-9]\{1,3\}\.sslip\.io' | head -1
```

## 5. Verify

Which checks apply depends on the mode. With `teardown_after` left on, the host no longer exists once the run ends, so the only evidence is the green run: check 1. Checks 2 to 4 need a deployment that persists (`teardown_after=false`).

1. **The pipeline's own checks passed**: the run finished green, including the smoke test, the hardening check and the analytics check. When `deploy_metabase` was on, the run also includes the dashboard smoke test and a second hardening check.
2. **Sign-in state, without values.** The command prints the response body and then the HTTP status:

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
4. **The web page** at `https://<host>/` loads over a valid certificate in a browser, and a customer sign-in reaches the chat. Copy the code with the command in step 6, paste it into the sign-in form, then clear the clipboard; use a persona not already used in check 3.
5. Record the run in the table at the end of this document.

## 6. Read the codes for the release message

This is the only way to get a code in a form that can be pasted (check 4 uses it too); check 3 reads the codes into a shell variable that is never displayed. Copy it straight to the clipboard so it is never printed, paste it into the message, then clear the clipboard. The subshell fails loudly if the read fails, instead of leaving an empty clipboard. On macOS:

```sh
( set -o pipefail; aws ssm get-parameter --name /transaction-disputes/prod/demo-signin-access-code --with-decryption --query Parameter.Value --output text | tr -d '\n' | pbcopy ) || echo "read failed: nothing was copied"
# paste into the message, then:
pbcopy </dev/null
```

The clipboard is not private: a clipboard manager may keep its history, and Universal Clipboard can copy the value to other devices signed in to the same account. Quit any clipboard manager and turn Handoff off for the duration, or clear the clipboard immediately after pasting.

Repeat for `demo-agent-access-code`. Send each code only in the release message itself; do not store it in a document, an issue or the repository.

## 7. After the release window

Turn the sign-in off by following [Turning the demonstration sign-in off](README.md#turning-the-demonstration-sign-in-off), or remove the whole host with `infra/scripts/07-teardown.sh` (the roles, repositories and seed bucket remain).

## 8. Daily model spend limit

The backend counts what each completed language-model call it makes for a customer costs and stops calling the model once a day's total reaches `LLM_DAILY_SPEND_LIMIT_USD` (default `10`, in US dollars). The day is the bank's operating day (America/Bogota), so the total resets at local midnight. The limit is a soft guard that sits under the provider's own hard monthly cap: calls already in flight when it trips still complete, so a day can end slightly above it.

While the limit is reached the service keeps answering. Understanding is unavailable, so a customer message is handed to a person with the usual review notice; the optional model renderer falls back to the template reply. Nothing goes silent. If the day's total cannot be read, the service treats the limit as reached rather than spending unmetered.

The total is the service's own customer traffic only. A call that fails after the provider has billed it, and the offline evaluation runs, spend against the provider's cap without appearing in it, so read the total as a lower bound on the provider's bill. While the limit is reached every new customer message becomes a handoff to a person until the next operating day; the queue fills with them, and they differ from a provider outage only in the log event `daily_spend_limit_reached`.

- **Change the limit** by setting `LLM_DAILY_SPEND_LIMIT_USD=<dollars>` in the `.env` file in `/opt/dispute-intake` on the host (create the file if it is absent; the compose file passes the value to the backend and defaults to `10`) and running a deploy again with `infra/scripts/05-deploy.sh`. It must be above zero. Do not run `docker compose` by hand on the host: only the deploy script exports the image, host-name and secret variables the compose files need. A deploy rewrites the compose files but not `.env`, so the value persists.
- **Read the day's total** from the `llm_spend_daily` table (`spend_day`, `spent_usd`).
- **Recognise a trip** in the backend log: `daily_spend_limit_reached` carries the day, the total and the limit; `daily_spend_unreadable` means the total could not be read, and `daily_spend_charge_failed` means a completed call could not be recorded (the reply is still delivered).

## Cost controls outside the repository

Two limits protect the monthly spend. No script creates them and the repository cannot show that they exist, so each is set by the maintainer in a console and checked by the command or the screen named here.

**Monthly cost alert on the AWS account.** In the AWS console, open *Billing and Cost Management*, then *Budgets*, then *Create budget*, choose *Customize (advanced)* and *Cost budget*, set the period to *Monthly* and the budget type to *Recurring budget* with a *Fixed* amount, and add email alerts on the actual spend and on the forecast spend. The intended values are a budget of US$50 with alerts at 50 %, 80 % and 100 % of actual spend and at 100 % of forecast spend. Check without changing anything:

```sh
account="$(aws sts get-caller-identity --query Account --output text)"
aws budgets describe-budgets --account-id "$account" --query 'Budgets[].[BudgetName,BudgetLimit.Amount,TimeUnit]' --output text
aws budgets describe-notifications-for-budget --account-id "$account" --budget-name "<name from the first command>" \
  --query 'Notifications[].[NotificationType,ComparisonOperator,ThresholdType,Threshold]' --output text
```

**Spend limit on the model provider.** In the Claude Console, open the organization's spend limit and notification settings (under *Settings*, then *Limits*; the screen names can differ by account) and set the monthly spend limit of the organization that owns the production key, with a notification below it and the automatic top-up the account offers. The values in force are a monthly limit of US$100, a notification at US$80 and an automatic reload of US$20 whenever the balance falls to US$5. They are set by the maintainer and are not verified from the repository: this limit lives in the provider's organization, so nothing the repository or the AWS account holds can read it back, and the screen is the only check. The monthly limit is the only ceiling on the model spend, because the automatic reload keeps the balance topped up until it is reached.

| Control | Last checked | Role | What was seen |
|---|---|---|---|
| AWS monthly cost alert | 2026-10-04 | Programmer, read-only commands above | One monthly cost budget of US$50 with notifications at 50 %, 80 % and 100 % of actual spend and at 100 % of forecast spend |
| Model provider spend limit | 2026-10-04 | Maintainer, reported; not read back by the programmer | Monthly limit of US$100, notification at US$80, automatic reload of US$20 when the balance falls to US$5. Not verifiable from the repository |

## Run record

One row per clean-account reproduction or persisting deployment, filled in by the maintainer after step 5. A row states only what its run did: a deployment onto an account that already holds the roles, repositories and seed bucket is not a clean-account reproduction and is not recorded as one.

| Date | Role | Mode | Outcome |
|---|---|---|---|
| 2026-10-04 | Programmer, dispatched at the maintainer's request | Persisting deployment from `main` at `2cad12d` (`teardown_after=false`, `deploy_metabase=true`) onto an account already provisioned; not a clean-account reproduction. Run `37227276409` | All jobs green: build and scan, deploy and smoke test, Metabase. Host `184-195-142-149.sslip.io`, dashboard on its `dashboard.` subdomain. The smoke test and both hardening checks passed. Checked by hand afterwards: a wrong access code is refused (401), a second sign-in of the same persona is refused (429), and the dashboard answers over a valid certificate with HSTS and a content security policy |
| 2026-10-04 | Dispatched from the maintainer's account | Persisting deployment from `main` at `79341e3` (`teardown_after=false`, `deploy_metabase=true`) onto an account already provisioned; not a clean-account reproduction. Run `37239401385` | All jobs green: build and scan, deploy and smoke test, Metabase; teardown skipped. Same host as the previous row |
| 2026-10-04 | Maintainer's dispatch | Persisting deployment from `main` at `ae78942` (`teardown_after=false`, `deploy_metabase=true`) onto an account already provisioned; not a clean-account reproduction. Run `37241751541` | All jobs green: build and scan, deploy and smoke test, Metabase; teardown skipped. Checked by hand afterwards: the persona list answers 200 and an unused customer persona signs in with a 201. Same host as the previous row |
