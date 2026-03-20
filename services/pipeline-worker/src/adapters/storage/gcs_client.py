import asyncio
from pathlib import Path
from typing import Any, Callable

from adapters.storage.client import StorageClient


class GCSStorageClient(StorageClient):
    def __init__(self, bucket_factory: Callable[[], Any]) -> None:
        self._bucket_factory = bucket_factory

    async def download_object(self, storage_path: str, destination: Path) -> None:
        await asyncio.to_thread(self._download_object_sync, storage_path, destination)

    def _download_object_sync(self, storage_path: str, destination: Path) -> None:
        bucket = self._bucket_factory()
        blob = bucket.blob(storage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(destination))

    async def upload_object(self, source: Path, storage_path: str) -> None:
        await asyncio.to_thread(self._upload_object_sync, source, storage_path)

    def _upload_object_sync(self, source: Path, storage_path: str) -> None:
        bucket = self._bucket_factory()
        blob = bucket.blob(storage_path)
        blob.upload_from_filename(str(source))

    async def delete_object(self, storage_path: str) -> None:
        await asyncio.to_thread(self._delete_object_sync, storage_path)

    def _delete_object_sync(self, storage_path: str) -> None:
        bucket = self._bucket_factory()
        bucket.blob(storage_path).delete()
