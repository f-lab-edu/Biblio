# ── Builder ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir poetry

WORKDIR /app

COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root

# ── Runtime ──────────────────────────────────────────────
FROM python:3.12-slim AS runtime

RUN groupadd --system appuser \
    && useradd --system --gid appuser --create-home --home-dir /home/appuser appuser

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser alembic.ini ./alembic.ini
COPY --chown=appuser:appuser alembic/ ./alembic/
COPY --chown=appuser:appuser src/ ./src/
RUN chmod -R a-w /app/.venv /app/alembic /app/src /app/alembic.ini

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE ${PORT:-8080}

CMD uvicorn src.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8080}
