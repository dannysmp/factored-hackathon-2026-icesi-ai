# Dispute Intake

AI-first **transaction-dispute intake** for a LATAM retail bank. A customer reports a problem
with a card or account transaction in Spanish, Portuguese or English. The system authenticates the
session, locates the transaction, collects the reason, decides what policy allows, files the case
when it is permitted, verifies the filing and hands the case to a human agent, with a structured
summary, whenever a person is required.

The guiding principle is that **AI is not autonomous just because it can be**: the language model
understands and renders language, while deterministic code decides and acts.

## How it works

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
| `app/` | FastAPI backend: validated configuration, composition root, health endpoints; home of the policy engine, tool layer and dialogue controller |
| `pipelines/` | Raw-data inventory and profiling; bronze → silver → gold jobs |
| `contracts/` | Versioned schemas for each source table |
| `policy/` | Dispute-policy rules (YAML) and the multilingual policy corpus generated from them |
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
| `make help` | List every target |

`make secrets` does not scan unstaged or untracked files: `git add` them first.

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
| `make profile`, `pipeline`, `analyze`, `train`, `evaluate` or `up` exits with code 2 | The target is not implemented yet |
