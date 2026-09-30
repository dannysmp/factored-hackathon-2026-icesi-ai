# =============================================================================
# Dockerfile — backend service image
# =============================================================================
# Purpose:
#   The FastAPI backend, built with `[project.dependencies]` in
#   pyproject.toml plus the `data` group (`duckdb`, for the deploy-time
#   seed and eval-bank loaders — see Design) and `pipelines/` (what those
#   loaders import). The `dev` and `ml` groups stay out (ADR-6: risk
#   scores are computed offline and stored, never imported at serving
#   time — the `ml` group's training dependencies have no reason to enter
#   this image at all). Neither ADR-6 nor any other ADR excludes `data`:
#   it is a single embedded read-only engine, already an accepted
#   architecture component (Postgres for operational state; Parquet and
#   DuckDB for analytics), not a training dependency.
# Design:
#   Multi-stage: uv resolves and installs into a virtual environment in the
#   builder stage; the final stage copies only that environment and the
#   application source, and runs as a non-root user. The model API key is
#   never built into the image: it reaches the container only as an
#   environment variable the deploy step sets from SSM Parameter Store.
#   `app.main`'s own import graph never touches `duckdb`, `load_seed` or
#   `load_eval_bank` — the running service carries the `data` group
#   without ever importing it at request-serving time; only the deploy
#   pipeline's one-off `docker compose run` containers invoke those two
#   modules directly, to migrate and seed the database.
# Usage:
#   docker build -t dispute-intake-backend .
#   docker run --rm -p 8000:8000 --env-file .env dispute-intake-backend
# =============================================================================

FROM python:3.11-slim AS builder

RUN pip install --no-cache-dir uv==0.12.19

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-default-groups --group data --no-install-project

COPY app ./app
COPY contracts ./contracts
COPY policy ./policy
COPY personas ./personas
COPY prompts ./prompts
COPY pipelines ./pipelines
RUN uv sync --frozen --no-default-groups --group data

FROM python:3.11-slim AS runtime

# The base image's own pip/setuptools/wheel bootstrap (never used at runtime: the venv below
# carries every dependency the app actually imports) vendors older copies of themselves that pick
# up their own CVEs over time; stripped here rather than patched piecemeal, since nothing running
# in this container ever calls pip or setuptools again. `pip uninstall` only removes the installed
# site-packages copies, not ensurepip's own bundled wheel archives (the same vendored code,
# unpacked again on the next `ensurepip` run) — removed explicitly, since a scanner that does not
# unpack nested wheels would otherwise miss it while it still ships in the image.
RUN python3 -m pip uninstall --yes pip setuptools wheel \
  && rm -rf /usr/local/lib/python3.11/ensurepip

RUN useradd --create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv .venv
COPY --from=builder --chown=app:app /app/app ./app
COPY --from=builder --chown=app:app /app/contracts ./contracts
COPY --from=builder --chown=app:app /app/policy ./policy
COPY --from=builder --chown=app:app /app/personas ./personas
COPY --from=builder --chown=app:app /app/prompts ./prompts
COPY --from=builder --chown=app:app /app/pipelines ./pipelines

ENV PATH="/app/.venv/bin:${PATH}"
USER app
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live')" || exit 1

# Exactly one worker, deliberately: app.security.sessions's revocation store is in-process memory,
# shared between requests but not between workers or processes. A second worker here would let a
# revoked session keep working against whichever worker didn't see the revocation. Add
# --workers only together with moving revocation to a shared store (the operational database).
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
