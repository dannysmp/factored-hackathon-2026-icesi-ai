# Dispute Intake

AI-first **transaction-dispute intake** for a LATAM retail bank. A customer reports a problem
with a transaction in Spanish, Portuguese or English; the system authenticates the session, locates
the transaction, decides deterministically what policy allows, files the case when permitted,
verifies the filing and escalates to a human with a structured packet when required.

Guiding constraint: **AI should not be autonomous just because it can be.** The model understands and
renders language; code decides and acts.

> **Status:** foundation only. The repository currently provides the service skeleton, configuration,
> quality gates and CI. Data pipelines, the policy engine, the conversation layer, evaluation and
> deployment land epic by epic; each directory below states which epic delivers it.

## Quickstart

Requirements: [uv](https://docs.astral.sh/uv/) and [gitleaks](https://github.com/gitleaks/gitleaks).

```bash
make setup      # install locked dependencies (Python 3.11) and create .env from the template
make lint       # ruff format check, ruff lint, mypy strict
make test       # hermetic tests with the coverage gate
make secrets    # secret scan of committed history and staged changes, plus a self-test
                # (unstaged and untracked files are not scanned: `git add` them first)
make audit      # dependency vulnerability scan
make run        # serve http://localhost:8000  (GET /health/live, GET /health/ready)
make help       # every target
```

Configuration is a single validated object (`app/config.py`); every variable is documented in
`.env.example`. The service starts without `ANTHROPIC_API_KEY`; it is required from the epic that
adds the LLM calls.

## Layout

| Path | Purpose | Delivered by |
|---|---|---|
| `app/` | FastAPI backend: configuration, composition root, later policy engine, tools, controller | E0 → E9 |
| `policy/` | Dispute-policy YAML and the multilingual corpus generated from it | E3 |
| `contracts/` | Versioned schemas per source table | E1 |
| `pipelines/` | Bronze → silver → gold jobs, profiling, fixtures | E1 |
| `models/` | Risk-model training, experiment log, model cards | E6 |
| `evals/` | Golden set, adversarial cases, harness, judge rubric | E8 |
| `web/` | Chat UI and human-agent console | E10 |
| `infra/` | AWS provisioning and deploy pipeline | E11 |
| `reports/` | Generated reports (git-ignored) | E1, E2, E8 |
| `docs/` | Public project documentation | E12 |

## Development workflow

Work lands through draft pull requests using `.github/pull_request_template.md`; each PR must pass
CI (`lint`, `test`, `secret-scan`) and the `review-gate` check before the maintainer merges it.
Commits and PRs carry only the maintainer's identity.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `uv: command not found` | Install uv: `brew install uv` or see the uv documentation |
| `gitleaks: command not found` when running `make secrets` | `brew install gitleaks` |
| `ConfigError: Invalid configuration — LOG_LEVEL: …` | The message names the bad key; fix it in `.env` (see `.env.example` for accepted values) |
| `make setup` fails with a stale lockfile | Run `uv lock` and commit the updated `uv.lock` |
| `make profile`, `pipeline`, `analyze`, `train`, `evaluate`, `up` exit with code 2 | Expected: they are delivered by the epic named in their message |
