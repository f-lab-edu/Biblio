# ── Builder ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir poetry

# venv를 런타임과 동일한 경로에 만들어, 실행파일 shebang이 처음부터 올바른
# 인터프리터 경로(/app/.venv/bin/python)를 가리키게 한다. 빌더에서 만든 뒤
# 다른 경로로 복사하면 shebang이 옛 경로를 가리켜 실행 시 깨진다.
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

WORKDIR /app/services/search-service

COPY packages/biblio-auth/ /app/packages/biblio-auth/
COPY services/search-service/pyproject.toml services/search-service/poetry.lock ./
RUN poetry install --only main --no-root

# ── Runtime ──────────────────────────────────────────────
FROM python:3.12-slim AS runtime

RUN groupadd --system appuser \
    && useradd --system --gid appuser --create-home --home-dir /home/appuser appuser

WORKDIR /app

COPY --from=builder --chown=root:root --chmod=0555 /app/.venv /app/.venv
COPY --chown=root:root --chmod=0555 services/search-service/src/ ./src/
COPY --chown=root:root --chmod=0555 services/search-service/docker-entrypoint.sh /app/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE ${PORT:-8082}

CMD ["/app/docker-entrypoint.sh"]
