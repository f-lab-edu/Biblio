# ── Builder ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir poetry

WORKDIR /app
WORKDIR /app/services/core-api

COPY packages/biblio-auth/ /app/packages/biblio-auth/
COPY services/core-api/pyproject.toml services/core-api/poetry.lock ./
RUN poetry install --only main --no-root

# ── Runtime ──────────────────────────────────────────────
FROM python:3.12-slim AS runtime

RUN groupadd --system appuser \
    && useradd --system --gid appuser --create-home --home-dir /home/appuser appuser

WORKDIR /app

COPY --from=builder --chown=root:root --chmod=0555 /app/services/core-api/.venv /app/.venv
COPY --chown=root:root --chmod=0444 services/core-api/alembic.ini ./alembic.ini
COPY --chown=root:root --chmod=0555 services/core-api/alembic/ ./alembic/
COPY --chown=root:root --chmod=0555 services/core-api/src/ ./src/
COPY --chown=root:root --chmod=0555 services/core-api/docker-entrypoint.sh /app/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE ${PORT:-8080}

CMD ["/app/docker-entrypoint.sh"]
