# =============================================================================
# Dockerfile — backend service image
# =============================================================================
# Purpose:
#   The FastAPI backend, built with exactly the runtime dependencies
#   (`[project.dependencies]` in pyproject.toml) and none of the dev, data or
#   ml groups (ADR-6: the ml group stays out of the runtime image; risk
#   scores are computed offline and stored, never imported at serving time).
# Design:
#   Multi-stage: uv resolves and installs into a virtual environment in the
#   builder stage; the final stage copies only that environment and the
#   application source, and runs as a non-root user. The model API key is
#   never built into the image (CR-10): it reaches the container only as an
#   environment variable the deploy step sets from SSM Parameter Store.
# Usage:
#   docker build -t dispute-intake-backend .
#   docker run --rm -p 8000:8000 --env-file .env dispute-intake-backend
# =============================================================================

FROM python:3.11-slim AS builder

RUN pip install --no-cache-dir uv==0.12.19

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-default-groups --no-install-project

COPY app ./app
COPY contracts ./contracts
COPY policy ./policy
RUN uv sync --frozen --no-default-groups

FROM python:3.11-slim AS runtime

RUN useradd --create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv .venv
COPY --from=builder --chown=app:app /app/app ./app
COPY --from=builder --chown=app:app /app/contracts ./contracts
COPY --from=builder --chown=app:app /app/policy ./policy

ENV PATH="/app/.venv/bin:${PATH}"
USER app
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live')" || exit 1

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
