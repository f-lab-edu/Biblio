from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Sequence

from src.infra.storage.client import ArtifactStore
from src.infra.storage.prefix import join_prefix, normalize_prefix, relative_name


class GCSArtifactStore(ArtifactStore):
    def __init__(self, *, bucket_name: str, client: Any | None = None) -> None:
        self._bucket_name = bucket_name
        self._client = client

    async def list_objects(self, prefix: str) -> Sequence[str]:
        return await asyncio.to_thread(self._list_objects, prefix)

    async def download_object(self, storage_path: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._download_object, storage_path, destination)

    async def upload_object(self, source: Path, storage_path: str) -> None:
        await asyncio.to_thread(self._upload_object, source, storage_path)

    async def copy_prefix(self, source_prefix: str, target_prefix: str) -> None:
        await asyncio.to_thread(self._copy_prefix, source_prefix, target_prefix)

    def object_uri(self, storage_path: str) -> str:
        return f"gs://{self._bucket_name}/{storage_path}"

    def _list_objects(self, prefix: str) -> list[str]:
        blobs = self._get_client().list_blobs(self._bucket_name, prefix=prefix)
        return sorted(blob.name for blob in blobs)

    def _download_object(self, storage_path: str, destination: Path) -> None:
        self._get_client().bucket(self._bucket_name).blob(storage_path).download_to_filename(str(destination))

    def _upload_object(self, source: Path, storage_path: str) -> None:
        self._get_client().bucket(self._bucket_name).blob(storage_path).upload_from_filename(str(source))

    def _copy_prefix(self, source_prefix: str, target_prefix: str) -> None:
        source_prefix = normalize_prefix(source_prefix)
        target_prefix = normalize_prefix(target_prefix)
        source_names = self._list_objects(source_prefix)
        if not source_names:
            raise FileNotFoundError(source_prefix)
        if self._list_objects(target_prefix):
            raise FileExistsError(target_prefix)

        bucket = self._get_client().bucket(self._bucket_name)
        for source_name in source_names:
            target_name = join_prefix(target_prefix, relative_name(source_name, source_prefix))
            bucket.copy_blob(bucket.blob(source_name), bucket, target_name)

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    @staticmethod
    def _build_client() -> Any:
        from google.cloud import storage

        return storage.Client()
