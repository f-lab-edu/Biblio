from __future__ import annotations

import shutil
from pathlib import Path
from typing import Sequence

from src.infra.storage.client import ArtifactStore
from src.infra.storage.prefix import join_prefix, normalize_prefix, relative_name


class LocalArtifactStore(ArtifactStore):
    def __init__(self, *, root_dir: Path, bucket_name: str = "local-feedback-loop") -> None:
        self._root_dir = root_dir
        self._bucket_name = bucket_name

    def _require_path_inside_root(self, path: Path) -> Path:
        root = self._root_dir.resolve()
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(root):
            raise ValueError(f"Path is outside root directory: {path}")
        return resolved_path

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
        source = self._require_path_inside_root(self._root_dir / storage_path)
        destination = self._require_path_inside_root(destination)
        if not source.exists():
            raise FileNotFoundError(storage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    async def upload_object(self, source: Path, storage_path: str) -> None:
        source = self._require_path_inside_root(source)
        destination = self._require_path_inside_root(self._root_dir / storage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    async def copy_prefix(self, source_prefix: str, target_prefix: str) -> None:
        source_prefix = normalize_prefix(source_prefix)
        target_prefix = normalize_prefix(target_prefix)
        self._require_path_inside_root(self._root_dir / source_prefix)
        self._require_path_inside_root(self._root_dir / target_prefix)
        source_files = await self.list_objects(source_prefix)
        if not source_files:
            raise FileNotFoundError(source_prefix)
        if await self.list_objects(target_prefix):
            raise FileExistsError(target_prefix)

        for source_name in source_files:
            destination_name = join_prefix(target_prefix, relative_name(source_name, source_prefix))
            source_path = self._require_path_inside_root(self._root_dir / source_name)
            destination_path = self._require_path_inside_root(self._root_dir / destination_name)
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination_path)

    def object_uri(self, storage_path: str) -> str:
        return f"gs://{self._bucket_name}/{storage_path}"
