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

COPY --from=builder /app/.venv /app/.venv
COPY src/ ./src/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE ${PORT:-8000}

CMD uvicorn src.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}
