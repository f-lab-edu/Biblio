from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.infra.storage import BlobMetadata, SignedUrlRequest, SignedUrlResult, StorageClient


class GCSStorageClient(StorageClient):
    def __init__(
        self,
        *,
        bucket_name: str,
        project_id: str,
        storage_client: Any | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._bucket_name = bucket_name
        self._project_id = project_id
        self._storage_client = storage_client or self._build_storage_client()
        self._bucket = self._storage_client.bucket(bucket_name)
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def _build_storage_client(self) -> Any:
        from google.cloud import storage

        return storage.Client(project=self._project_id)

    def generate_signed_url(self, request: SignedUrlRequest) -> SignedUrlResult:
        blob = self._bucket.blob(request.object_name)
        expires_at = self._now_provider() + timedelta(seconds=request.expires_in_seconds)
        method = "PUT" if request.operation == "upload" else "GET"
        kwargs: dict[str, Any] = {
            "version": "v4",
            "expiration": timedelta(seconds=request.expires_in_seconds),
            "method": method,
        }

        if request.operation == "upload" and request.content_type is not None:
            kwargs["content_type"] = request.content_type
        if request.max_size_bytes is not None:
            kwargs["headers"] = {
                "x-goog-content-length-range": f"0,{request.max_size_bytes}",
            }

        url = blob.generate_signed_url(**kwargs)
        return SignedUrlResult(url=url, expires_at=expires_at)

    def get_blob_metadata(self, object_name: str) -> BlobMetadata:
        blob = self._bucket.blob(object_name)
        if not blob.exists():
            return BlobMetadata(exists=False)

        blob.reload()
        return BlobMetadata(
            exists=True,
            size_bytes=getattr(blob, "size", None),
            etag=getattr(blob, "etag", None),
        )

    def delete_object(self, object_name: str) -> bool:
        from google.api_core.exceptions import NotFound

        blob = self._bucket.blob(object_name)
        try:
            blob.delete()
        except NotFound:
            return False
        return True
