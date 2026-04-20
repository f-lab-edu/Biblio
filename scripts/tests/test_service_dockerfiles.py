from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("dockerfile_path", "expected_copy_lines"),
    [
        (
            ROOT / "services" / "core-api" / "Dockerfile",
            [
                "COPY --from=builder --chown=root:root --chmod=0555 /app/.venv /app/.venv",
                "COPY --chown=root:root --chmod=0444 alembic.ini ./alembic.ini",
                "COPY --chown=root:root --chmod=0555 alembic/ ./alembic/",
                "COPY --chown=root:root --chmod=0555 src/ ./src/",
            ],
        ),
        (
            ROOT / "services" / "search-service" / "Dockerfile",
            [
                "COPY --from=builder --chown=root:root --chmod=0555 /app/.venv /app/.venv",
                "COPY --chown=root:root --chmod=0555 src/ ./src/",
                "COPY --chown=root:root --chmod=0555 docker-entrypoint.sh /app/docker-entrypoint.sh",
                'CMD ["/app/docker-entrypoint.sh"]',
            ],
        ),
        (
            ROOT / "services" / "pipeline-worker" / "Dockerfile",
            [
                "&& groupadd --system appuser \\",
                "COPY --from=builder --chown=root:root --chmod=0555 /app/.venv /app/.venv",
                "COPY --chown=root:root --chmod=0555 src/ ./src/",
                'CMD ["python", "-m", "src.main"]',
            ],
        ),
        (
            ROOT / "services" / "managed-embedding-endpoint" / "Dockerfile",
            [
                "COPY --from=builder --chown=root:root --chmod=0555 /app/.venv /app/.venv",
                "COPY --chown=root:root --chmod=0555 src/ ./src/",
                "COPY --chown=root:root --chmod=0555 docker-entrypoint.sh /app/docker-entrypoint.sh",
                'CMD ["/app/docker-entrypoint.sh"]',
            ],
        ),
        (
            ROOT / "services" / "core-api" / "Dockerfile",
            [
                "COPY --chown=root:root --chmod=0555 docker-entrypoint.sh /app/docker-entrypoint.sh",
                'CMD ["/app/docker-entrypoint.sh"]',
            ],
        ),
    ],
)
def test_runtime_dockerfiles_copy_app_artifacts_as_read_only_root_owned(
    dockerfile_path: Path,
    expected_copy_lines: list[str],
) -> None:
    content = dockerfile_path.read_text(encoding="utf-8")

    for expected_copy_line in expected_copy_lines:
        assert expected_copy_line in content


def test_pipeline_worker_dockerfile_avoids_split_runtime_setup_run_blocks() -> None:
    dockerfile_path = ROOT / "services" / "pipeline-worker" / "Dockerfile"
    content = dockerfile_path.read_text(encoding="utf-8")

    assert "RUN groupadd --system appuser" not in content
