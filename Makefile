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

.PHONY: help setup lint format test test-all secrets audit run clean \
        profile pipeline analyze features corpus corpus-check train evaluate up \
        db-up db-down migrate test-integration

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

test-integration: ## Tests needing a real Postgres (DATABASE_URL must point at a migrated one)
	$(RUN) pytest -m integration

secrets: ## Scan history + staged changes (NOT unstaged/untracked files), then run the self-test
	gitleaks git . --config .gitleaks.toml --no-banner --redact
	gitleaks git . --config .gitleaks.toml --no-banner --redact --pre-commit --staged
	bash scripts/verify_secret_scan.sh

audit: ## Dependency vulnerability scan
	$(RUN) pip-audit

run: ## Run the service locally with reload
	$(RUN) uvicorn app.main:create_app --factory --reload --port 8000

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis .coverage htmlcov
	find . -type d -name __pycache__ -not -path './.venv/*' -prune -exec rm -rf {} +

profile: ## Profile the raw data (DATA_DIR, default data/raw) and write reports/data-profile.md
	$(RUN) python -m pipelines.profile --data-dir $(DATA_DIR)

pipeline: ## Clean the raw data into typed Parquet (SILVER_DIR, default data/silver) and write reports/data-quality.md
	$(RUN) python -m pipelines.silver --raw $(DATA_DIR) --out $(SILVER_DIR)

analyze: ## Build the dispute marts from SILVER_DIR and write reports/workflow-analysis.md
	$(RUN) python -m pipelines.analysis --silver $(SILVER_DIR)

features: ## Build the risk feature mart from SILVER_DIR and write reports/risk-features.md
	$(RUN) python -m pipelines.risk_features --silver $(SILVER_DIR)

corpus: ## Regenerate the multilingual policy corpus in policy/corpus from the policy YAML
	$(RUN) python -m pipelines.policy_corpus

corpus-check: ## Fail when policy/corpus differs from what the policy generates
	$(RUN) python -m pipelines.policy_corpus --check

# ---- Not yet implemented (fail loudly until they are) --------------------------

train: ## Train and log the risk model
	@echo "make train is not implemented yet" >&2; exit 2

evaluate: ## Run the evaluation harness: make evaluate SYSTEM={P|B0|B1}
	@echo "make evaluate is not implemented yet" >&2; exit 2

up: ## Start the full stack with docker compose
	@echo "make up is not implemented yet" >&2; exit 2
