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

        kwargs.update(self._resolve_signing_kwargs())
        url = blob.generate_signed_url(**kwargs)
        return SignedUrlResult(url=url, expires_at=expires_at)

    def _resolve_signing_kwargs(self) -> dict[str, Any]:
        """서명 방식을 런타임 자격증명에 따라 자동으로 고른다.

        로컬은 서비스계정 키 파일(비공개 키 보유)이라 라이브러리가 직접 서명한다.
        Cloud Run은 키 없이 토큰만 있으므로, IAM signBlob에 서명을 위임한다.
        직접 서명이 가능하면 빈 dict를 돌려줘 기존 동작을 그대로 유지한다.
        """

        credentials = getattr(self._storage_client, "_credentials", None)
        if credentials is None or self._can_sign_locally(credentials):
            return {}

        import google.auth
        from google.auth.transport.requests import Request

        # signBlob 호출에는 cloud-platform 범위 토큰이 필요하다.
        # 스토리지 클라이언트의 토큰은 스토리지 범위로 좁아 권한이 모자라므로,
        # cloud-platform 범위로 새로 발급받아 서명에만 쓴다.
        signing_credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        signing_credentials.refresh(Request())
        return {
            "service_account_email": signing_credentials.service_account_email,
            "access_token": signing_credentials.token,
        }

    @staticmethod
    def _can_sign_locally(credentials: Any) -> bool:
        """비공개 키로 직접 서명할 수 있는 자격증명인지 판별한다."""

        from google.oauth2 import service_account

        return isinstance(credentials, service_account.Credentials)

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
