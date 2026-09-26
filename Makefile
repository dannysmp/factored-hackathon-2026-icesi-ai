# =============================================================================
# Makefile — single entry point for local development and CI
# =============================================================================
# Purpose:  One command surface; CI calls these same targets, so "works locally"
#           and "works in CI" mean the same thing.
# Requires: uv (https://docs.astral.sh/uv/), gitleaks (secret scan), docker (later epics).
# Usage:    make help
# Notes:    Targets for epics that have not landed yet fail loudly with the epic that
#           delivers them; they never pretend to succeed.
# =============================================================================

.DEFAULT_GOAL := help
SHELL := /bin/bash
RUN := uv run

.PHONY: help setup lint format test test-all secrets audit run clean \
        profile pipeline analyze train evaluate up

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

secrets: ## Scan committed history and staged changes for secrets, then prove the scanner works
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

# ---- Targets delivered by later epics (fail loudly until then) ----------------

profile: ## (E1) Profile the raw data and write reports/data-profile.md
	@echo "make profile is delivered by epic E1" >&2; exit 2

pipeline: ## (E1) Run the bronze -> silver -> gold pipeline
	@echo "make pipeline is delivered by epic E1" >&2; exit 2

analyze: ## (E2) Regenerate reports/workflow-analysis.md
	@echo "make analyze is delivered by epic E2" >&2; exit 2

train: ## (E6) Train and log the risk model
	@echo "make train is delivered by epic E6" >&2; exit 2

evaluate: ## (E8) Run the evaluation harness: make evaluate SYSTEM={P|B0|B1}
	@echo "make evaluate is delivered by epic E8" >&2; exit 2

up: ## (E11) Start the full stack with docker compose
	@echo "make up is delivered by epic E11 (compose stack)" >&2; exit 2
