from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import UUID


class WorkdirManager:
    """Creates per-video temporary directories that are always cleaned up."""

    def __init__(self, base_dir: Path | str | None = None, prefix: str = "pipeline_worker_workdirs"):
        self.base_dir = Path(base_dir or tempfile.gettempdir()) / prefix
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _normalize_video_id(self, video_id: UUID | str) -> str:
        return "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in str(video_id))

    @contextmanager
    def temporary(self, video_id: UUID | str) -> Iterator[Path]:
        """Yield a fresh directory for the supplied `video_id` and delete it afterwards."""

        sanitized = self._normalize_video_id(video_id)
        workdir = self.base_dir / sanitized
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            yield workdir
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
