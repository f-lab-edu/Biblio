from pathlib import Path
from typing import Any, Callable

from adapters.storage.client import StorageClient


class GCSStorageClient(StorageClient):
    def __init__(self, bucket_factory: Callable[[], Any]) -> None:
        self._bucket_factory = bucket_factory

    async def download_object(self, storage_path: str, destination: Path) -> None:
        bucket = self._bucket_factory()
        blob = bucket.blob(storage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(destination))

    async def upload_object(self, source: Path, storage_path: str) -> None:
        bucket = self._bucket_factory()
        blob = bucket.blob(storage_path)
        blob.upload_from_filename(str(source))

    async def delete_object(self, storage_path: str) -> None:
        bucket = self._bucket_factory()
        bucket.blob(storage_path).delete()
