from __future__ import annotations

from pathlib import Path
from typing import Sequence

from src.infra.storage.client import ArtifactStore


class LocalArtifactStore(ArtifactStore):
    def __init__(self, *, root_dir: Path, bucket_name: str = "local-feedback-loop") -> None:
        self._root_dir = root_dir
        self._bucket_name = bucket_name

    async def list_objects(self, prefix: str) -> Sequence[str]:
        root = self._root_dir
        if not root.exists():
            return []
        return sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path.relative_to(root).as_posix().startswith(prefix)
        )

    async def download_object(self, storage_path: str, destination: Path) -> None:
        source = self._root_dir / storage_path
        if not source.exists():
            raise FileNotFoundError(storage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    async def upload_object(self, source: Path, storage_path: str) -> None:
        destination = self._root_dir / storage_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    def object_uri(self, storage_path: str) -> str:
        return f"gs://{self._bucket_name}/{storage_path}"
