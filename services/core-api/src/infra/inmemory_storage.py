from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from src.infra.storage import BlobMetadata, SignedUrlRequest, SignedUrlResult, StorageClient


@dataclass(slots=True)
class StoredObject:
    content: bytes
    etag: str


class InMemoryStorageClient(StorageClient):
    def __init__(self, now_provider: Callable[[], datetime] | None = None) -> None:
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._objects: dict[str, StoredObject] = {}
        self.generated_requests: list[SignedUrlRequest] = []

    def put_object(self, object_name: str, content: bytes, *, etag: str | None = None) -> None:
        self._objects[object_name] = StoredObject(content=content, etag=etag or str(uuid4()))

    def generate_signed_url(self, request: SignedUrlRequest) -> SignedUrlResult:
        self.generated_requests.append(request)
        expires_at = self._now_provider() + timedelta(seconds=request.expires_in_seconds)
        method = "put" if request.operation == "upload" else "get"
        return SignedUrlResult(
            url=f"https://inmemory-storage.local/{request.object_name}?method={method}",
            expires_at=expires_at,
        )

    def get_blob_metadata(self, object_name: str) -> BlobMetadata:
        stored_object = self._objects.get(object_name)
        if stored_object is None:
            return BlobMetadata(exists=False)

        return BlobMetadata(
            exists=True,
            size_bytes=len(stored_object.content),
            etag=stored_object.etag,
        )

    def delete_object(self, object_name: str) -> bool:
        return self._objects.pop(object_name, None) is not None
