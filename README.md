# Dispute Intake

AI-first **transaction-dispute intake** for a LATAM retail bank. A customer reports a problem
with a card or account transaction in Spanish, Portuguese or English. The system authenticates the
session, locates the transaction, collects the reason, decides what policy allows, files the case
when it is permitted, verifies the filing and hands the case to a human agent, with a structured
summary, whenever a person is required.

The guiding principle is that **AI is not autonomous just because it can be**: the language model
understands and renders language, while deterministic code decides and acts.

## How it works

This section describes the target design; the implementation status of each control is tracked in
[SECURITY.md](SECURITY.md).

Every customer message goes through the same five stages:

| Stage | What happens | Where it lives |
|---|---|---|
| **Understand** | Detect the language, extract the intent and slots as structured output, keep conversation state | Language layer (LLM with a strict output schema) |
| **Decide** | Apply the dispute policy: eligibility, confirmation requirements, routing; each decision carries a stable reason code | Policy engine (pure, deterministic code) |
| **Act** | Call tools scoped to the authenticated customer; filing a case needs explicit confirmation and an idempotency key | Tool layer with authorization middleware |
| **Verify** | Read back every write before reporting it to the customer | Dialogue controller |
| **Escalate** | Transfer to a human with the request, verified facts, actions taken, evidence and open questions | Handoff builder and agent console |

Design rules that follow from this:

- Policy and permissions are enforced in code and in the tool layer, never in prompts.
- A document number alone never proves identity; access is bound to an authenticated session.
- The model may only use facts and sources that the decision layer put in its input, and its
  wording must agree with the decisions taken.
- A learned risk score can route a case to human review; it never decides an outcome.

## Repository structure

| Path | Purpose |
|---|---|
| `app/` | FastAPI backend: validated configuration, composition root, health endpoints; the dispute policy engine (`app/domain/policy`); home of the tool layer and dialogue controller |
| `pipelines/` | Raw-data inventory and profiling; the cleaning stage that produces typed, de-duplicated Parquet tables, quarantine and manifests; the dispute marts and the workflow analysis |
| `contracts/` | Versioned data contract: allowed values, ranges, canonical spellings and reference handling for each source table |
| `policy/` | The versioned dispute-policy parameters (YAML) and the multilingual policy corpus generated from them |
| `models/` | Risk-model training code, experiment log and model cards |
| `evals/` | Golden set, adversarial cases, evaluation harness and judge rubric |
| `web/` | Customer chat and human-agent console |
| `infra/` | AWS provisioning and deployment pipeline |
| `docs/` | Project documentation |
| `reports/` | Generated reports (data profile, analyses, evaluation results) |
| `scripts/` | Repository tooling, such as the secret-scan self-test |
| `tests/` | Hermetic unit and contract tests |

## Getting started

### Prerequisites

| Tool | What it is used for | Install |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Python package and environment manager. It downloads Python 3.11, installs the exact dependency versions pinned in `uv.lock` and runs every command in that environment, so every machine and CI run behaves the same. | `brew install uv` |
| [gitleaks](https://github.com/gitleaks/gitleaks) | Secret scanner. `make secrets` and CI use it to make sure no credential or key is ever committed to the repository. | `brew install gitleaks` |

Docker is not needed yet; it is required once the containerized stack is added.

### Set up and run

```bash
make setup      # install locked dependencies (Python 3.11) and create .env from the template
make run        # serve http://localhost:8000  (GET /health/live, GET /health/ready)
```

### Development commands

| Command | What it does |
|---|---|
| `make lint` | Format check, lint and strict type-check |
| `make test` | Fast hermetic tests with a coverage gate |
| `make secrets` | Scan committed history and staged changes for secrets, then run the scanner self-test |
| `make audit` | Dependency vulnerability scan |
| `make profile` | Profile the raw data and write `reports/data-profile.md` |
| `make pipeline` | Clean the raw data into typed Parquet and write `reports/data-quality.md` |
| `make analyze` | Build the dispute marts from the cleaned layer and write `reports/workflow-analysis.md` |
| `make corpus` | Regenerate the multilingual policy corpus in `policy/corpus` from the policy YAML |
| `make corpus-check` | Fail when the committed corpus differs from what the policy generates |
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

### Configuration

Configuration is a single validated object (`app/config.py`) loaded from the environment and an
optional `.env` file. Every variable is documented in `.env.example`. The service starts without
`ANTHROPIC_API_KEY`; it is only required once a feature makes an LLM call. Invalid configuration
fails at startup with a message that names the offending key and never echoes its value.

## Quality and security

Every change passes formatting, linting, strict type-checking, tests with a coverage gate, a
dependency audit and a full-history secret scan in CI before it is reviewed. The security
requirements, and which controls exist today, are in [SECURITY.md](SECURITY.md).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `uv: command not found` | Install uv: `brew install uv` or see the uv documentation |
| `gitleaks: command not found` when running `make secrets` | `brew install gitleaks` |
| `ConfigError: Invalid configuration — LOG_LEVEL: …` | The message names the bad key; fix it in `.env` (see `.env.example` for accepted values) |
| `make setup` fails with a stale lockfile | Run `uv lock` and commit the updated `uv.lock` |
| `make profile` fails with `cannot load table …` or `lacks key column` | The message names the table; check that the raw files match the layout described under *Data* |
| `make pipeline` exits with code 1 | A table could not be cleaned; `reports/data-quality.md` names it and the reason (for example a file without a header row) |
| `make analyze` exits with code 1 and `cleaned table … not found` | Run `make pipeline` first: the analysis reads the cleaned layer |
| `make train`, `evaluate` or `up` exits with code 2 | The target is not implemented yet |
