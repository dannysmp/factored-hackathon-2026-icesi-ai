# Dispute intake

AI-first **transaction-dispute intake** for a LATAM retail bank. A customer reports a problem
with a card or account transaction in Spanish, Portuguese or English. The system authenticates the
session, locates the transaction, collects the reason, decides what policy allows, files the case
when it is permitted, verifies the filing and hands the case to a human agent, with a structured
summary, whenever a person is required.

The guiding principle is that **AI is not autonomous just because it can be**: the language model
understands and renders language, while deterministic code decides and acts.

## How it works

The design below is what the system implements. The status of each security control, including
what is only partly met, is tracked in [SECURITY.md](SECURITY.md).

Every customer message goes through the same five stages:

| Stage | What happens | Where it lives |
|---|---|---|
| **Understand** | Detect the language, extract the intent and slots as structured output, keep conversation state | Language layer (LLM with a strict output schema) |
| **Decide** | Apply the dispute policy: eligibility, confirmation requirements, routing; each decision carries a stable reason code | Policy engine (pure, deterministic code) |
| **Act** | Call tools scoped to the authenticated customer; filing a case needs explicit confirmation and an idempotency key | Tool layer with authorization middleware |
| **Verify** | Read back every write before reporting it to the customer | Dialogue controller |
| **Escalate** | Transfer to a human with the request, verified facts, actions taken, evidence and open questions | Handoff builder and agent console |

```mermaid
flowchart LR
    customer["Customer<br/>(es / pt / en)"] --> chat["Chat"]
    agent["Human agent"] --> console["Agent console<br/>(read-only viewer)"]
    chat --> caddy["Caddy<br/>(HTTPS)"]
    console --> caddy
    caddy --> api["FastAPI backend<br/>(session and authorization)"]
    api --> controller["Dialogue controller<br/>(state machine)"]

    controller -- "1 Understand" --> nlu["Language model<br/>(structured output)"]
    controller -- "2 Decide, 3 Act, 4 Verify" --> tools["Tool layer<br/>(scoped to the session's customer)"]
    tools --> policy["Policy engine<br/>(pure code)"]
    controller -- "5 Escalate" --> handoff["Handoff builder<br/>(pure code)"]
    handoff --> outbox["Handoff outbox"]
    controller --> render["Renderer and<br/>output verifier"]
    render -. "optional, off by default" .-> nlu

    tools --> db[("Postgres<br/>operational state, audit trail")]
    outbox --> db
    api -- "agent queue and ticket reads" --> db
    risk["Risk score<br/>(not wired: routing is off<br/>in the shipped policy)"] -. "routes to review only" .-> policy
    render --> reply["Reply in the customer's language"]
    reply --> chat
```

Design rules that follow from this:

- Policy and permissions are enforced in code and in the tool layer, never in prompts.
- A document number alone never proves identity; access is bound to an authenticated session.
- The model may only use facts and sources that the decision layer put in its input, and its
  wording must agree with the decisions taken.
- A learned risk score can route a case to human review; it never decides an outcome.

## Repository structure

| Path | Purpose |
|---|---|
| `app/` | FastAPI backend: validated configuration, composition root, health, sign-in and session routes; the dialogue controller (`app/conversation`), the dispute policy engine (`app/domain/policy`), the tool layer (`app/tools`), model providers (`app/llm`), policy retrieval (`app/retrieval`), persistence (`app/persistence`), and the reliability, security and observability controls |
| `pipelines/` | Raw-data inventory and profiling; the cleaning stage that produces typed, de-duplicated Parquet tables, quarantine and manifests; the dispute marts and the workflow analysis |
| `contracts/` | Versioned data contract: allowed values, ranges, canonical spellings and reference handling for each source table |
| `policy/` | The versioned dispute-policy parameters (YAML) and the multilingual policy corpus generated from them |
| `personas/` | The closed set of demonstration personas the sign-in accepts |
| `prompts/` | The versioned model prompts: understanding, rendering and the evaluation judge |
| `models/` | Risk-model training code, experiment log and model cards |
| `evals/` | Golden set, adversarial cases, evaluation harness and judge rubric |
| `web/` | React customer chat and human-agent console (a read-only viewer of the handoff queue) |
| `infra/` | AWS provisioning and deployment scripts, the reverse-proxy configuration and the deployment runbook; the pipeline itself is `.github/workflows/deploy.yml` |
| `docs/` | Limitations, the security checklist, the release checklist, the demonstration scripts, the video script, the slide content and the delivery message |
| `reports/` | Generated reports (data profile, analyses, operational seed, evaluation results) |
| `Dockerfile`, `docker-compose*.yml` | The backend image, the local Postgres serving store, and the deployed stack composed on top of it |
| `scripts/` | Repository tooling, such as the secret-scan self-test |
| `tests/` | Hermetic unit and contract tests |

### Discipline map

Each engineering discipline this project touches has one artifact that is the current, checkable
evidence of its state — not a status claim, but something a reader can open and verify directly:

| Discipline | Open this |
|---|---|
| Data engineering | [`reports/data-profile.md`](reports/data-profile.md), [`reports/data-quality.md`](reports/data-quality.md) |
| Backend and policy | `app/domain/policy/`, [SECURITY.md](SECURITY.md), [the ASVS Level 1 checklist](docs/asvs-level1-checklist.md) |
| Language understanding and dialogue | `app/conversation/`, `app/llm/`, `app/retrieval/` |
| Machine learning | [`models/model_card.json`](models/model_card.json), [`models/README.md`](models/README.md) |
| Evaluation | [`reports/evaluation.md`](reports/evaluation.md), generated by `make evaluate FULL=1` from the golden set in `evals/golden/` |
| Frontend | [`web/README.md`](web/README.md) |
| Deployment | [`infra/README.md`](infra/README.md), [`infra/deployment-runbook.md`](infra/deployment-runbook.md) |
| Release readiness | [`docs/release-checklist.md`](docs/release-checklist.md), [`docs/limitations.md`](docs/limitations.md) |

## Getting started

### Prerequisites

| Tool | What it is used for | Install |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Python package and environment manager. It downloads Python 3.11, installs the exact dependency versions pinned in `uv.lock` and runs every command in that environment, so every machine and CI run behaves the same. | `brew install uv` |
| [gitleaks](https://github.com/gitleaks/gitleaks) | Secret scanner. `make secrets` and CI use it to make sure no credential or key is ever committed to the repository. | `brew install gitleaks` |
| [Docker](https://docs.docker.com/get-docker/) | Runs the local Postgres serving store (`make db-up`) and builds the images. Not needed for a first look at the service. | Docker Desktop |
| [Node.js](https://nodejs.org/) 22 | Builds, lints and tests `web/` (`web/.nvmrc`). Not needed for the backend. | `brew install node@22` |

### Set up and run

```bash
make setup                              # install locked dependencies (Python 3.11), create .env
DATA_AS_OF_DATE=2026-06-18 make run     # serve http://localhost:8000
curl http://localhost:8000/health/ready
```

The service fixes the date it treats as "today" at start-up, the data's reference date, so a result
never drifts with the wall clock. It reads that date from `DATA_AS_OF_DATE` or, when that is empty,
from the loaded operational seed, and refuses to start when neither resolves. This is enough for
the health endpoints. A conversation needs the serving store.

#### The full backend on your machine

This needs the raw data (see *Data*) and Docker.

```bash
make pipeline && make seed   # clean the raw data, then build the operational seed in data/gold/ops_seed
make db-up                   # local Postgres on port 5432
# set DATABASE_URL in .env: postgresql://dispute_intake:dispute_intake@localhost:5432/dispute_intake
make migrate && make load-seed
make run
```

Also set in `.env`: `ANTHROPIC_API_KEY`, or `LLM_PROVIDER=stub` to replace the model with a
deterministic keyword classifier (no model call; refused when `APP_ENV=prod`); and one way to sign
in, either the sandbox login or a demonstration sign-in (see *Authentication and sessions*).
`make db-down` stops the store and deletes its data.

The web interface calls the API on its own origin and the Vite dev server has no proxy, so it runs
as part of the deployed stack (see *Deployment*); locally, exercise the API directly. Frontend
commands are in [`web/README.md`](web/README.md).

### Development commands

| Command | What it does |
|---|---|
| `make setup` | Install the locked dependencies and create `.env` from the template |
| `make run` | Serve the API on port 8000 with reload |
| `make clean` | Remove caches and build artifacts |
| `make lint` | Format check, lint and strict type-check |
| `make format` | Apply the formatter and safe lint fixes |
| `make test` | Fast hermetic tests with a coverage gate |
| `make test-all` | Every test except the live smoke tests, including those that need Postgres |
| `make test-integration` | Tests that need a migrated Postgres (`DATABASE_URL`) |
| `make secrets` | Scan committed history and staged changes for secrets, then run the scanner self-test |
| `make semgrep` | Static security analysis; blocks on error-severity findings |
| `make audit` | Dependency vulnerability scan |
| `make db-up`, `make db-down` | Start the local Postgres serving store; stop it and delete its data |
| `make migrate` | Apply pending serving-store migrations |
| `make check-migrations` | Fail when a new migration's numeric prefix collides with one on the base branch |
| `make check-infra-scripts` | Shellcheck and a bash syntax check of every script in `infra/scripts` (needs `shellcheck`) |
| `make profile` | Profile the raw data and write `reports/data-profile.md` |
| `make pipeline` | Clean the raw data into typed Parquet and write `reports/data-quality.md` |
| `make analyze` | Build the dispute marts from the cleaned layer and write `reports/workflow-analysis.md` |
| `make features` | Build the risk feature mart from the cleaned layer and write `reports/risk-features.md` |
| `make train` | Run the risk signal probe, the model comparison and the calibration, and write the model card |
| `make seed` | Build the operational seed from the cleaned layer and write `reports/ops-seed.md` |
| `make eval-bank` | Write the frozen evaluation scenario bank |
| `make load-seed`, `make load-eval-bank`, `make load-analytics` | Load the operational seed, the scenario bank and the dispute marts into Postgres |
| `make seed-ci-smoke` | Seed the synthetic data the CI smoke subset needs into a migrated Postgres |
| `make reset-demo-personas` | Delete the demonstration personas' accumulated cases before a demonstration |
| `make corpus` | Regenerate the multilingual policy corpus in `policy/corpus` from the policy YAML |
| `make corpus-check` | Fail when the committed corpus differs from what the policy generates |
| `make evaluate` | Run the evaluation harness (see *Evaluation*) |
| `make judge-validation RATER1=<csv> RATER2=<csv>` | Score the returned rater sheets with the real judge and patch `reports/evaluation.md` |
| `make up` | A placeholder that exits with code 2; the deployed stack is composed by `infra/` |
| `make help` | List every target |

`make secrets` does not scan unstaged or untracked files: `git add` them first.

### Data

The source data is a set of CSV files that are never committed. Place them under `data/raw`, or
point `DATA_DIR` at another location:

```text
data/raw/
├── customers.csv, products.csv, branches.csv, …      one file per dimension table
└── transactions/year=YYYY/month=MM/day=DD/transactions_YYYYMMDD.csv
    (and the same layout for the other daily fact tables)
```

The data commands rewrite reports that are committed, so they refuse to run when their input
directory is absent: `make profile` and `make pipeline` stop with a message when `data/raw` (or
`DATA_DIR`) does not exist, and `make analyze` and `make features` stop when `data/silver` (or
`SILVER_DIR`) does not exist. Nothing is changed in that case. An existing but empty directory is
treated as data. `make profile` and `make pipeline` then run and write reports that list every
table as missing, and `make analyze` removes `reports/workflow-analysis.md` before failing;
`git checkout -- reports` restores them.

`make profile` measures the files against the data dictionary and writes
[`reports/data-profile.md`](reports/data-profile.md): row counts, schema conformance, duplicate
keys, missing and malformed values, referential integrity, arrival lateness and the workload
facts (fraud prevalence, USD amount consistency, transcript availability, complaint categories),
with a verdict on each assumption made about the data. The profile is deterministic, so a change
in the report reflects a change in the data.

`make pipeline` turns the raw files into the cleaned layer under `data/silver` (or `SILVER_DIR`):

| Folder | Content |
|---|---|
| `silver/` | One typed Parquet file per table, de-duplicated (the latest version of each key wins) and satisfying the contract |
| `quarantine/` | The rows that did not, with the reason code (`required`, `type`, `value`, `range`, `reference`) |
| `extras/` | Values of columns the contract does not declare, kept beside the cleaned table |
| `manifests/` | One JSON file per table: input digest, contract and code version, row counts, reasons and output digests |

The stage is deterministic and idempotent: the same raw files, contract version and code version
give byte-identical outputs, and a table whose inputs and outputs are unchanged is not rebuilt.
Every raw row is either kept, superseded by a newer version of its key, or quarantined with a
reason; `reports/data-quality.md` summarises the counts.

`make analyze` reads the cleaned layer and writes [`reports/workflow-analysis.md`](reports/workflow-analysis.md):
how much of the service demand is about disputed transactions, how those cases are resolved today,
how customers feel about the contacts, what handling a dispute costs under stated assumptions
(`pipelines/analysis_assumptions.toml`) and which outcomes an automated workflow should reach. It
builds the aggregate marts under `data/gold/dispute_demand`; the report holds counts and rates only.

`make features` reads the cleaned layer and builds the table the transaction risk model learns from
(`data/gold/risk_features`), with the report [`reports/risk-features.md`](reports/risk-features.md).
Every feature uses only what was known when the transaction happened (the velocity windows end
strictly before it), the source's own fraud score and the authorisation outcome are left out on
purpose, and every row carries its training, validation or test period from `models/split.toml`.

`make seed` builds `data/gold/ops_seed`, the operational seed the running service reads: a curated,
deterministic selection of active customers with their products and transactions, with direct
identifiers masked and a reference date, described in [`reports/ops-seed.md`](reports/ops-seed.md).
It is built on a developer machine from the cleaned layer, never in CI or on the deployed host.
`make eval-bank` writes the frozen scenario bank the adversarial evaluation cases need.

#### Where each input comes from

Every input the system reads or was built from falls into exactly one of four classes, stated here
so a reader never has to guess:

| Class | What it means | Examples in this repository |
|---|---|---|
| **Real** | An actual record from the provided source dataset, unmodified in substance | `data/raw` and `data/silver`; `data/gold/ops_seed`'s stratified sample of customers, products and transactions |
| **De-identified** | A real record with direct identifiers masked or removed before use | `ops_seed`'s masked email and phone columns. The conversation never asks for a document number and a document number is never accepted as proof of identity; one a customer volunteers in free text is redacted by shape before the model sees it, with disclosed gaps (see [`docs/limitations.md`](docs/limitations.md)) |
| **Synthetic** | Fabricated data standing in for a condition the real data cannot hold, or a parameter set written for this project rather than sourced from any institution | the frozen adversarial scenarios defined as literals for the evaluation harness (an orphan transaction, a poisoned field, an unconvertible amount); the dispute policy's own thresholds and windows (`policy/dispute_policy_v1.yaml`), which state their own synthetic provenance |
| **Team-generated** | Natural-language content the team wrote, because the source data carries no conversational or dispute-related text at all | the golden set's scripted customer turns (`evals/golden/`); the multilingual policy corpus (`policy/corpus/`) |

A case, a case sheet or a report states its own provenance inline wherever more than one class could
plausibly apply to the same row.

### Model cards

Every model this system trains or scores with is described by a machine-readable model card,
written by the training job itself so the card can never drift from what was actually run: the
selected model, its calibration, the routing threshold and why it was or was not set, the test-period
result and its confidence interval, and the model's own data provenance and limitations. The
transaction-risk model's current card is [`models/model_card.json`](models/model_card.json); see
[`models/README.md`](models/README.md) for what produced it and how to reproduce it.

### Evaluation

The golden set is 135 scripted cases in Spanish, Portuguese and English, 32 of them adversarial
(prompt injection, poisoned retrieval, cross-customer access). Each case is run against three
systems: **P**, this system; **B0**, the same service with the model replaced by a deterministic
keyword classifier; and **B1**, a naive model agent that chooses its own tool calls and outcomes, with no dialogue controller. Scoring is by
recorded outcome, and every unsafe outcome is counted.

```bash
make evaluate SYSTEM=P SMOKE=1    # the 16-case injection and authorization subset, one system, once
make evaluate SYSTEM={P|B0|B1}    # one system over the whole golden set
make evaluate FULL=1              # P three times, B0 and B1 once; writes reports/evaluation.md
```

A run needs a migrated Postgres loaded with `make load-seed` (and `make load-eval-bank` for the full
adversarial set), `TEST_IDENTITY_ENABLED=true` with a `TEST_IDENTITY_KEY` (the harness never turns
the sandbox login on itself), and `ANTHROPIC_API_KEY` for B1 and for P unless `LLM_PROVIDER=stub`.
`make judge-validation` also calls the judge model and needs the key. Any run in which a case
turns unsafe exits with code 1. CI runs the 16-case subset for P and B0 against stubbed models on
every pull request and blocks the merge on a failure.
`make evaluate FULL=1` calls the live model many times and costs real money.

[`reports/evaluation.md`](reports/evaluation.md) is the committed result of the last full run:
workload, versions, headline metrics per system, repeated-run variability, the failure gallery,
unsafe outcomes, judge validation, learned components and limitations. Every figure is measured
offline on the golden set. Two human raters and the judge scored the same 50 cases (6 for
clarification), and the judge agreed with them on too few to be trusted on any dimension, so the
report states "not reportable by the judge" and shows the raters' means instead.

| Metric (cases) | P | B0 | B1 |
|---|---|---|---|
| Runs | 3 | 1 | 1 |
| Safe automated resolution (103 in scope) | 0.725 (range 0.718-0.728) | 0.330 | 0.369 |
| Containment (103) | 0.809 (range 0.806-0.816) | 0.845 | 0.757 |
| Escalation quality (22 needing a person) | 0.727 | 0.455 | 0.273 |
| Missed transfers (22 needing a person) | 0.152 (range 0.136-0.182) | 0.545 | 0.091 |
| Unnecessary transfers (81 not needing one) | 0.012 | 0.074 | 0.062 |
| Unsafe outcomes (135) | 0.000 | 0.000 | 0.000 |
| Latency, median / 95th percentile | 2.558 s / 4.402 s | 0.027 s / 0.046 s | 5.105 s / 12.217 s |
| Cost per attempted case (USD) | 0.005 | 0.000 | 0.008 |

P is the mean of its three runs; B0 and B1 ran once, so their figures carry no run-to-run range.
Zero observed unsafe outcomes means none occurred in the case-runs counted, not that the risk is
zero. B0 shares the dialogue controller with P and is not an unchanged control.
[`reports/evaluation-comparison.md`](reports/evaluation-comparison.md) sets this run beside the two
earlier ones and lists the cases that still fail. The figures describe the commit the report names
(`6cea3b4`), not the current head, which carries later behaviour fixes and whose effect is not
measured. See [`docs/limitations.md`](docs/limitations.md).

### Configuration

Configuration is a single validated object (`app/config.py`) loaded from the environment and an
optional `.env` file. `.env.example` lists the variables an operator sets; `app/config.py`
defines every variable and its default. The service starts without `ANTHROPIC_API_KEY`; it is
only required once a feature makes an LLM call. Anything that reads the serving store needs
`DATABASE_URL`, and the service needs a domain date (see *Set up and run*). Invalid configuration
fails at startup with a message that names the offending key and never echoes its value.

### Authentication and sessions

Every path under `/v1/` requires a session (default deny); only the sign-in routes that are switched
on are public, and adding a route later does not change that. A session is a short-lived signed
token that binds a request to one principal, never to a parameter of the request. A customer
session (`/v1/session`, `/v1/turns`) carries the customer; an agent session (`/v1/agent/*`) carries
the agent, and neither is accepted for the other's routes.

| Endpoint | Purpose |
|---|---|
| `POST /v1/turns` | One conversation turn; the customer comes from the session |
| `GET /v1/session` | Who the session belongs to and when it ends |
| `POST /v1/auth/logout` | Ends the session immediately |
| `GET /v1/auth/demo-personas` | The closed list of demonstration personas; public while either demonstration sign-in is on |
| `POST /v1/auth/demo-sessions` | Customer demonstration sign-in: body `{"persona": "<slug>"}` and the header `X-Demo-Access-Code`. Exists only when `DEMO_SIGNIN_ENABLED=true`. The session lasts 30 minutes |
| `POST /v1/auth/demo-agent-sessions` | The same for the console, with its own code and signing key. Exists only when `DEMO_AGENT_SIGNIN_ENABLED=true`. The session lasts 60 minutes |
| `POST /v1/auth/test-sessions` | Sandbox login for trusted test clients: body `{"customer_id": "..."}` and the header `X-Test-Login-Key`. Exists only when `TEST_IDENTITY_ENABLED=true` (never in `prod`); failed attempts are limited per client address |
| `GET /v1/agent/queue`, `GET /v1/agent/tickets/{ref}` | The console's reads: the handoff queue and one ticket |
| `POST /v1/agent/tickets/{ref}/claim`, `…/release`, `…/notes`, `…/status` | The console's narrow writes, scoped to the signed-in agent and audited. The console interface does not call them |

The demonstration sign-in is how the deployed system is shown: a visitor picks a persona from
`personas/demo_personas_v1.yaml` and proves they were given the access code, which is held outside
the repository and gates the demonstration only. Real authentication and authorization apply
whether or not the code is known. Either demonstration sign-in and the sandbox login are
mutually exclusive, and the backend refuses to start when the two access codes, or the agent
signing key and the session signing key, are the same value. Wrong codes count against the caller's
address and are rate-limited, and each persona holds one live session at a time.

Send the token as `Authorization: Bearer <token>`. A missing, invalid, expired or ended session
gets `401` with `reauth_required: true`, so a client knows to sign in again. An identifier a
customer types (a document number, a name) is never accepted as proof of identity: the sandbox login
rejects any field but `customer_id`, and real deployments receive sessions from the bank's
identity provider.

Failed sandbox logins are counted per client address, taken from the last `X-Forwarded-For` entry the reverse proxy sets and falling back to the connecting address when the header is absent; the demo sign-in routes use the same address, and the limiter is per process. The shared secret is always compared before anything is counted, so a correct key succeeds however many wrong keys the address has sent, and a success never clears another client's recorded failures on a shared address.

Every failure has the same shape (an RFC 9457 problem document) with a stable `code`, a safe message
and a `request_id`; the same identifier is in the `X-Request-ID` response header, which a client may
also supply. Set `SESSION_SIGNING_KEY` (at least 32 characters, for example `openssl rand -hex 32`);
in `local` a throw-away key is used when it is empty, in `dev` and `prod` the service refuses to
start without it.

## Deployment

The deployed system is one AWS host (`t3.large`, region `us-east-1`) running the backend, the web
interface, Postgres and a Caddy reverse proxy with automatic TLS in docker compose, reachable at
`<address-with-dashes>.sslip.io` with no domain to buy. The Metabase operations dashboard is an
optional addition. `.github/workflows/deploy.yml` runs on demand: it builds both images, scans them
with Trivy, pushes them to ECR using GitHub's OIDC identity (no static AWS keys anywhere), then
deploys over Systems Manager (no SSH): the database is migrated and loaded with the operational seed
from a private bucket before the rest of the stack is brought up. It finishes with a smoke test and
a hardening check against the live address. The host is removed at the end of
the run unless it is started with `teardown_after=false`.

```bash
gh workflow run deploy.yml --ref main -f teardown_after=false -f deploy_metabase=false
```

Every secret, including the model key and the demonstration access codes, lives in AWS Systems
Manager Parameter Store under `/transaction-disputes/prod/` and is read by the host with its own
role. The data provider's credentials exist only in a developer's local AWS profile. What each
script creates, and the one-time prerequisites, are in [`infra/README.md`](infra/README.md); the
order of the steps, the checks after each and how to read the codes back for a release message are
in [`infra/deployment-runbook.md`](infra/deployment-runbook.md).

## Quality and security

Every change passes formatting, linting, strict type-checking, tests with a coverage gate, a
dependency audit, static security analysis, a full-history secret scan, the web interface's lint
and tests, a shell-script lint of `infra/scripts`, container builds and the evaluation smoke subset
in CI before it is reviewed. A deployment is additionally checked against the live address. The
security requirements, and which controls exist today, are in [SECURITY.md](SECURITY.md) and the
[ASVS Level 1 checklist](docs/asvs-level1-checklist.md); what the system does not do is in
[`docs/limitations.md`](docs/limitations.md).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `uv: command not found` | Install uv: `brew install uv` or see the uv documentation |
| `gitleaks: command not found` when running `make secrets` | `brew install gitleaks` |
| `ConfigError: Invalid configuration — LOG_LEVEL: …` | The message names the bad key; fix it in `.env` (see `.env.example` for accepted values) |
| `make setup` fails with a stale lockfile | Run `uv lock` and commit the updated `uv.lock` |
| `make profile` finishes but a table is listed as not profiled, with `could not be parsed`, `InvalidHeader` or `HeaderMismatch` | The report names the table and the reason; check that its raw file matches the layout described under *Data* |
| `make profile` exits with code 1 and `lacks key column(s)` | The message names the table; check that its raw file carries the key columns described under *Data* |
| `make pipeline` exits with code 1 and `reports/data-quality.md` exists | A table could not be cleaned; the report names it and the reason (for example a file without a header row) |
| `make pipeline` exits non-zero and `reports/data-quality.md` is missing | The build crashed before finishing; a report from an earlier run is removed rather than left stale, so its absence is the crash's own signal. Check the traceback |
| `make analyze` exits non-zero | Run `make pipeline` first: the analysis reads the cleaned layer. Any earlier `reports/workflow-analysis.md` is removed rather than left stale, so its absence is expected; `analysis_failed` in the log names a handled reason, otherwise check the traceback |
| `make profile` or `make pipeline` stops with `no raw data at …` | Place the CSV files under `data/raw`, or set `DATA_DIR` to where they are. No report was changed |
| `make analyze` or `make features` stops with `no cleaned layer at …` | Run `make pipeline` with the raw data in place first, or set `SILVER_DIR`. No report was changed |
| `make features` exits non-zero and logs `risk_features_failed reason=FileNotFoundError` | The cleaned layer exists but lacks a table the features read. Run `make pipeline` with the complete raw data |
| The service exits with `No domain date resolves` | Set `DATA_AS_OF_DATE` (for example `2026-06-18`) in `.env`, or load the operational seed (`make load-seed`) so the service can read it |
| `DATABASE_URL is required for this operation but is not set` | Start the store (`make db-up`) and set `DATABASE_URL` in `.env` to its address |
| The service exits with `SESSION_SIGNING_KEY is required` | Set `SESSION_SIGNING_KEY` in `.env` (32 or more characters); only `APP_ENV=local` may start without it |
| Every request answers `401` with `session_expired` | Sessions last `SESSION_TTL_SECONDS` (default 15 minutes) for the sandbox login, 30 minutes for a customer demonstration sign-in and 60 for an agent; sign in again |
| `make evaluate` stops with a message naming `TEST_IDENTITY_ENABLED` | The harness signs in through the sandbox login and never enables it; set `TEST_IDENTITY_ENABLED=true` and a `TEST_IDENTITY_KEY` of at least 16 characters |
| `make up` exits with code 2 | The target is a placeholder; the deployed stack is composed by `infra/` (see *Deployment*) |
