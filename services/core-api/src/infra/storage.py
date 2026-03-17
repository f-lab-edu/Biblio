from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

DEFAULT_SIGNED_URL_TTL_SECONDS = 30 * 60
MAX_UPLOAD_SIZE_BYTES = 2 * 1024 * 1024 * 1024
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
