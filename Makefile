# =============================================================================
# Makefile — single entry point for local development and CI
# =============================================================================
# Purpose:  One command surface; CI calls these same targets, so "works locally"
#           and "works in CI" mean the same thing.
# Requires: uv (https://docs.astral.sh/uv/), gitleaks (secret scan), docker (compose stack).
# Usage:    make help
# Notes:    Targets for capabilities that are not implemented yet fail loudly instead of
#           pretending to succeed.
# =============================================================================

.DEFAULT_GOAL := help
SHELL := /bin/bash
RUN := uv run
DATA_DIR ?= data/raw
SILVER_DIR ?= data/silver
SEMGREP_VERSION := 1.178.0

.PHONY: help setup lint format test test-all secrets semgrep audit run clean \
        profile pipeline analyze features corpus corpus-check train evaluate up \
        db-up db-down migrate check-migrations check-infra-scripts test-integration seed \
        eval-bank load-seed load-analytics reset-demo-personas seed-ci-smoke \
        judge-validation require-raw-data require-silver

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Install locked deps (incl. dev) and create .env from the template
	uv sync --frozen
	@test -f .env || cp .env.example .env

lint: ## Format check, lint and strict type-check
	$(RUN) ruff format --check .
	$(RUN) ruff check .
	$(RUN) mypy

format: ## Auto-format and apply safe lint fixes
	$(RUN) ruff format .
	$(RUN) ruff check --fix .

test: ## Fast hermetic tests (unit + contract) with the coverage gate
	$(RUN) pytest -m "not integration and not eval and not smoke" --cov --cov-report=term-missing

test-all: ## All tests except live smoke (integration + recorded evals)
	$(RUN) pytest -m "not smoke"

db-up: ## Start the local Postgres serving store
	docker compose up -d postgres

db-down: ## Stop the local Postgres serving store and remove its volume
	docker compose down -v

migrate: ## Apply pending serving-store migrations (needs DATABASE_URL)
	$(RUN) python -m app.persistence.migrate

check-migrations: ## Fail if a new migration's numeric prefix collides with one already on BASE (default origin/main)
	$(RUN) python -m scripts.check_migration_prefixes --base $(or $(BASE),origin/main)

check-infra-scripts: ## Shellcheck and a bash-3.2 syntax check on every infra/scripts/*.sh (needs shellcheck)
	set -e; cd infra/scripts && for f in *.sh lib/*.sh; do echo "shellcheck: $$f"; shellcheck -x "$$f"; done
	set -e; cd infra/scripts && for f in *.sh lib/*.sh; do echo "bash -n: $$f"; bash -n "$$f"; done

test-integration: ## Tests needing a real Postgres (DATABASE_URL must point at a migrated one)
	$(RUN) pytest -m integration

seed: ## Build the operational seed from SILVER_DIR and write reports/ops-seed.md
	$(RUN) python -m pipelines.ops_seed --silver $(SILVER_DIR)

eval-bank: ## Write the frozen evaluation scenario bank
	$(RUN) python -m pipelines.eval_bank

load-seed: ## Load the built operational seed into Postgres (needs DATABASE_URL, already migrated)
	$(RUN) python -m app.persistence.load_seed

load-eval-bank: ## Load the built eval bank into Postgres, additively (run after load-seed for a full evaluation run)
	$(RUN) python -m app.persistence.load_eval_bank

load-analytics: ## Load the dispute-demand marts into the Postgres analytics schema (needs DATABASE_URL, already migrated)
	$(RUN) python -m pipelines.analytics_load

reset-demo-personas: ## Delete demo personas' accumulated cases before a demonstration (needs DATABASE_URL)
	$(RUN) python -m app.persistence.reset_demo_personas

secrets: ## Scan history + staged changes (NOT unstaged/untracked files), then run the self-test
	gitleaks git . --config .gitleaks.toml --no-banner --redact
	gitleaks git . --config .gitleaks.toml --no-banner --redact --pre-commit --staged
	bash scripts/verify_secret_scan.sh

semgrep: ## Static analysis (security-audit, OWASP Top 10, secrets); blocks on ERROR-severity findings only
	uvx semgrep==$(SEMGREP_VERSION) scan --config p/security-audit --config p/owasp-top-ten \
		--config p/secrets --severity ERROR --error .

audit: ## Dependency vulnerability scan
	$(RUN) pip-audit

run: ## Run the service locally with reload
	$(RUN) uvicorn app.main:create_app --factory --reload --port 8000

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis .coverage htmlcov
	find . -type d -name __pycache__ -not -path './.venv/*' -prune -exec rm -rf {} +

# Without the data the pipeline modules finish "successfully" on an empty run and overwrite the
# committed reports, so the data commands stop here first.
require-raw-data:
	@test -d "$(DATA_DIR)" || { echo "make: no raw data at $(DATA_DIR); place the CSV files there or set DATA_DIR (see README, Data). Nothing was changed." >&2; exit 1; }

require-silver:
	@test -d "$(SILVER_DIR)" || { echo "make: no cleaned layer at $(SILVER_DIR); run 'make pipeline' with the raw data in place or set SILVER_DIR (see README, Data). Nothing was changed." >&2; exit 1; }

profile: require-raw-data ## Profile the raw data (DATA_DIR, default data/raw) and write reports/data-profile.md
	$(RUN) python -m pipelines.profile --data-dir $(DATA_DIR)

pipeline: require-raw-data ## Clean the raw data into typed Parquet (SILVER_DIR, default data/silver) and write reports/data-quality.md
	$(RUN) python -m pipelines.silver --raw $(DATA_DIR) --out $(SILVER_DIR)

analyze: require-silver ## Build the dispute marts from SILVER_DIR and write reports/workflow-analysis.md
	$(RUN) python -m pipelines.analysis --silver $(SILVER_DIR)

features: require-silver ## Build the risk feature mart from SILVER_DIR and write reports/risk-features.md
	$(RUN) python -m pipelines.risk_features --silver $(SILVER_DIR)

corpus: ## Regenerate the multilingual policy corpus in policy/corpus from the policy YAML
	$(RUN) python -m pipelines.policy_corpus

corpus-check: ## Fail when policy/corpus differs from what the policy generates
	$(RUN) python -m pipelines.policy_corpus --check

train: ## Run the risk signal probe, the boosted-model comparison and calibration; write the model card
	$(RUN) python -m models.probe
	$(RUN) python -m models.boosted
	$(RUN) python -m models.calibration

evaluate: ## Run the evaluation harness: make evaluate SYSTEM={P|B0|B1} [SMOKE=1], or make evaluate FULL=1 [SMOKE=1]
	$(RUN) python -m evals.cli $(if $(FULL),--full,--system $(SYSTEM)) $(if $(SMOKE),--smoke,)

judge-validation: ## Score the returned judge-validation sheets with the real judge and patch reports/evaluation.md: make judge-validation RATER1=<csv> RATER2=<csv> [REPORT=<md>] [CASES=<csv>] (needs ANTHROPIC_API_KEY)
	$(if $(and $(RATER1),$(RATER2)),,$(error RATER1 and RATER2 are required))
	$(RUN) python -m evals.h4_judge_validation --rater1 "$(RATER1)" --rater2 "$(RATER2)" \
		$(if $(REPORT),--report "$(REPORT)",) $(if $(CASES),--cases "$(CASES)",)

seed-ci-smoke: ## Seed the CI-only synthetic data the smoke subset needs (needs DATABASE_URL, migrated)
	$(RUN) python -m tests.fixtures.ci_smoke_seed

# ---- Not yet implemented (fail loudly until it is) --------------------------

up: ## Start the full stack with docker compose
	@echo "make up is not implemented yet" >&2; exit 2
