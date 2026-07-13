import asyncio
from pathlib import Path
from typing import Any, Callable

from src.infra.storage.client import StorageClient

GCS_BATCH_DELETE_SIZE = 100


class GCSStorageClient(StorageClient):
    def __init__(self, bucket_factory: Callable[[], Any], *, bucket_name: str) -> None:
        self._bucket_factory = bucket_factory
        self._bucket_name = bucket_name

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
        await self.delete_objects([storage_path])

    async def delete_objects(self, storage_paths: list[str]) -> None:
        await asyncio.to_thread(self._delete_objects_sync, storage_paths)

    def _delete_objects_sync(self, storage_paths: list[str]) -> None:
        unique_paths = list(dict.fromkeys(storage_paths))
        for start in range(0, len(unique_paths), GCS_BATCH_DELETE_SIZE):
            chunk = unique_paths[start:start + GCS_BATCH_DELETE_SIZE]
            bucket = self._bucket_factory()
            client = bucket.client
            batch = client.batch(raise_exception=False)
            with batch:
                for storage_path in chunk:
                    bucket.blob(storage_path).delete()
            failed_statuses = self._batch_delete_failed_statuses(batch)
            if failed_statuses:
                raise RuntimeError(f"GCS batch delete failed with statuses: {failed_statuses}")

    def object_uri(self, storage_path: str) -> str:
        return f"gs://{self._bucket_name}/{storage_path}"

    @staticmethod
    def _batch_delete_failed_statuses(batch: Any) -> list[int]:
        responses = getattr(batch, "_responses", ())
        return [
            response.status_code
            for response in responses
            if response.status_code not in {200, 204, 404}
        ]
