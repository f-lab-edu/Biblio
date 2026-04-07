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
            ],
        ),
        (
            ROOT / "services" / "pipeline-worker" / "Dockerfile",
            [
                "COPY --from=builder --chown=root:root --chmod=0555 /app/.venv /app/.venv",
                "COPY --chown=root:root --chmod=0555 src/ ./src/",
            ],
        ),
        (
            ROOT / "services" / "managed-embedding-endpoint" / "Dockerfile",
            [
                "COPY --from=builder --chown=root:root --chmod=0555 /app/.venv /app/.venv",
                "COPY --chown=root:root --chmod=0555 src/ ./src/",
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
