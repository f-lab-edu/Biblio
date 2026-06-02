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
    && useradd --system --gid appuser --create-home --home-dir /home/appuser appuser \
    && mkdir -p /var/lib/feedback-loop/artifacts \
    && chown -R appuser:appuser /var/lib/feedback-loop

WORKDIR /app

COPY --from=builder --chown=root:root --chmod=0555 /app/.venv /app/.venv
COPY --chown=root:root --chmod=0555 src/ ./src/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOCAL_ARTIFACT_ROOT=/var/lib/feedback-loop/artifacts

USER appuser

CMD ["python", "-m", "src.main"]
