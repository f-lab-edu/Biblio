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

RUN mkdir -p /home/appuser/.cache/huggingface \
    && chown -R appuser:appuser /home/appuser/.cache

COPY --from=builder --chown=root:root --chmod=0555 /app/.venv /app/.venv
COPY --chown=root:root --chmod=0555 src/ ./src/
COPY --chown=root:root --chmod=0555 docker-entrypoint.sh /app/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE ${PORT:-8000}

CMD ["/app/docker-entrypoint.sh"]
