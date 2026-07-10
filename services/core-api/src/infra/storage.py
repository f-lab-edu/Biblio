from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

DEFAULT_SIGNED_URL_TTL_SECONDS = 30 * 60
MAX_UPLOAD_SIZE_BYTES = 500 * 1024 * 1024
StorageOperation = Literal["upload", "download"]


@dataclass(frozen=True, slots=True)
class SignedUrlRequest:
    object_name: str
    operation: StorageOperation
    expires_in_seconds: int = DEFAULT_SIGNED_URL_TTL_SECONDS
    content_type: str | None = "application/octet-stream"
    max_size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class SignedUrlResult:
    url: str
    expires_at: datetime
    required_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BlobMetadata:
    exists: bool
    size_bytes: int | None = None
    etag: str | None = None


class StorageClient(ABC):
    @abstractmethod
    def generate_signed_url(self, request: SignedUrlRequest) -> SignedUrlResult:
        raise NotImplementedError

    @abstractmethod
    def get_blob_metadata(self, object_name: str) -> BlobMetadata:
        raise NotImplementedError

    @abstractmethod
    def delete_object(self, object_name: str) -> bool:
        raise NotImplementedError


def build_signed_url_required_headers(request: SignedUrlRequest) -> dict[str, str]:
    headers: dict[str, str] = {}
    if request.operation == "upload" and request.content_type is not None:
        headers["content-type"] = request.content_type
    if request.max_size_bytes is not None:
        headers["x-goog-content-length-range"] = f"0,{request.max_size_bytes}"
    return headers
