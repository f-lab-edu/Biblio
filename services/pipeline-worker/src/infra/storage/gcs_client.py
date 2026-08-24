import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from google.api_core.exceptions import PreconditionFailed

from src.infra.storage.client import MediaInput, StorageClient

GCS_BATCH_DELETE_SIZE = 100


class GCSStorageClient(StorageClient):
    def __init__(self, bucket_factory: Callable[[], Any], *, bucket_name: str) -> None:
        self._bucket_factory = bucket_factory
        self._bucket_name = bucket_name

    def create_media_input(
        self,
        storage_path: str,
        *,
        expires_in_seconds: int,
        expected_generation: str | None = None,
    ) -> MediaInput:
        if expires_in_seconds <= 0:
            raise ValueError("expires_in_seconds must be positive")
        blob = self._bucket_factory().blob(storage_path)
        blob.reload()
        generation = str(blob.generation or "")
        if not generation:
            raise RuntimeError("GCS source object has no generation")
        if expected_generation is not None and generation != expected_generation:
            raise RuntimeError("Normalization source generation changed during retry")
        if getattr(blob, "content_encoding", None) not in {None, "", "identity"}:
            raise RuntimeError("GCS source object must not use content encoding")
        signing_kwargs = self._resolve_signing_kwargs(blob)
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=expires_in_seconds),
            method="GET",
            query_parameters={"generation": generation},
            **signing_kwargs,
        )
        return MediaInput(url=url, generation=generation)

    @staticmethod
    def _resolve_signing_kwargs(blob: Any) -> dict[str, Any]:
        credentials = getattr(blob.client, "_credentials", None)
        if credentials is None:
            return {}

        from google.oauth2 import service_account

        if isinstance(credentials, service_account.Credentials):
            return {}

        import google.auth
        from google.auth.transport.requests import Request

        signing_credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        signing_credentials.refresh(Request())
        return {
            "service_account_email": signing_credentials.service_account_email,
            "access_token": signing_credentials.token,
        }

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

    async def upload_object_if_absent(
        self,
        source: Path,
        storage_path: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._upload_object_if_absent_sync,
            source,
            storage_path,
        )

    def _upload_object_if_absent_sync(
        self,
        source: Path,
        storage_path: str,
    ) -> bool:
        blob = self._bucket_factory().blob(storage_path)
        try:
            blob.upload_from_filename(str(source), if_generation_match=0)
        except PreconditionFailed:
            return False
        return True

    async def object_exists(self, storage_path: str) -> bool:
        return await asyncio.to_thread(self._object_exists_sync, storage_path)

    def _object_exists_sync(self, storage_path: str) -> bool:
        return bool(self._bucket_factory().blob(storage_path).exists())

    async def list_objects(self, prefix: str) -> list[str]:
        return await asyncio.to_thread(self._list_objects_sync, prefix)

    def _list_objects_sync(self, prefix: str) -> list[str]:
        return [blob.name for blob in self._bucket_factory().list_blobs(prefix=prefix)]

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
