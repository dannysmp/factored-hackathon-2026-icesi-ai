# Dispute Intake

AI-first **transaction-dispute intake** for a LATAM retail bank. A customer reports a problem
with a transaction in Spanish, Portuguese or English; the system authenticates the session, locates
the transaction, decides deterministically what policy allows, files the case when permitted,
verifies the filing and escalates to a human with a structured packet when required.

Guiding constraint: **AI should not be autonomous just because it can be.** The model understands and
renders language; code decides and acts.

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
`.env.example`. The service starts without `ANTHROPIC_API_KEY`; it is only required once a feature
makes an LLM call.

## Layout

| Path | Purpose | Status |
|---|---|---|
| `app/` | FastAPI backend: configuration, composition root; later the policy engine, tools and controller | In progress |
| `pipelines/` | Bronze → silver → gold jobs, profiling, fixtures | In progress |
| `contracts/` | Versioned schemas per source table | Planned |
| `policy/` | Dispute-policy YAML and the multilingual corpus generated from it | Planned |
| `models/` | Risk-model training, experiment log, model cards | Planned |
| `evals/` | Golden set, adversarial cases, harness, judge rubric | Planned |
| `web/` | Chat UI and human-agent console | Planned |
| `infra/` | AWS provisioning and deploy pipeline | Planned |
| `reports/` | Generated reports | Planned |
| `docs/` | Public project documentation | Planned |

## Contributing

Changes land through pull requests using `.github/pull_request_template.md`. Every pull request
must pass CI (`lint`, `test`, `secret-scan`) and an independent code review before it is merged.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `uv: command not found` | Install uv: `brew install uv` or see the uv documentation |
| `gitleaks: command not found` when running `make secrets` | `brew install gitleaks` |
| `ConfigError: Invalid configuration — LOG_LEVEL: …` | The message names the bad key; fix it in `.env` (see `.env.example` for accepted values) |
| `make setup` fails with a stale lockfile | Run `uv lock` and commit the updated `uv.lock` |
| `make pipeline`, `analyze`, `train`, `evaluate` or `up` exits with code 2 | The target is not implemented yet |
