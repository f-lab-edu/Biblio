from datetime import UTC, datetime, timedelta

from src.infra.gcs_client import GCSStorageClient
from src.infra.inmemory_storage import InMemoryStorageClient
from src.infra.storage import MAX_UPLOAD_SIZE_BYTES, SignedUrlRequest


class FakeBlob:
    def __init__(self, generated_url: str) -> None:
        self.generated_url = generated_url
        self.generate_signed_url_calls: list[dict[str, object]] = []
        self.deleted = False
        self._exists = False
        self.size: int | None = None
        self.etag: str | None = None

    def generate_signed_url(self, **kwargs: object) -> str:
        self.generate_signed_url_calls.append(kwargs)
        return self.generated_url

    def exists(self) -> bool:
        return self._exists

    def reload(self) -> None:
        return None

    def delete(self) -> None:
        self.deleted = True


class FakeBucket:
    def __init__(self, blob: FakeBlob) -> None:
        self._blob = blob

    def blob(self, object_name: str) -> FakeBlob:
        self.object_name = object_name
        return self._blob


class FakeStorageClient:
    def __init__(self, bucket: FakeBucket) -> None:
        self._bucket = bucket

    def bucket(self, bucket_name: str) -> FakeBucket:
        self.bucket_name = bucket_name
        return self._bucket


def test_gcs_storage_client_generates_upload_signed_url_with_ttl_and_size_limit() -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    blob = FakeBlob("https://signed.example/upload")
    storage_client = FakeStorageClient(FakeBucket(blob))
    client = GCSStorageClient(
        bucket_name="video-bucket",
        project_id="project-id",
        storage_client=storage_client,
        now_provider=lambda: now,
    )

    result = client.generate_signed_url(
        SignedUrlRequest(
            object_name="videos/user/video/original.mp4",
            operation="upload",
            max_size_bytes=MAX_UPLOAD_SIZE_BYTES,
        )
    )

    assert result.url == "https://signed.example/upload"
    assert result.expires_at == now + timedelta(minutes=30)
    assert result.required_headers == {
        "content-type": "application/octet-stream",
        "x-goog-content-length-range": f"0,{MAX_UPLOAD_SIZE_BYTES}",
    }
    assert blob.generate_signed_url_calls == [
        {
            "version": "v4",
            "expiration": timedelta(minutes=30),
            "method": "PUT",
            "content_type": "application/octet-stream",
            "headers": {
                "x-goog-content-length-range": f"0,{MAX_UPLOAD_SIZE_BYTES}",
            },
        }
    ]


def test_inmemory_storage_client_tracks_metadata_and_delete() -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    client = InMemoryStorageClient(now_provider=lambda: now)
    client.put_object("videos/user/video/original.mp4", b"video-bytes", etag="etag-1")

    signed = client.generate_signed_url(
        SignedUrlRequest(
            object_name="videos/user/video/original.mp4",
            operation="download",
        )
    )
    metadata = client.get_blob_metadata("videos/user/video/original.mp4")

    assert signed.url.endswith("method=get")
    assert signed.expires_at == now + timedelta(minutes=30)
    assert signed.required_headers == {}
    assert metadata.exists is True
    assert metadata.size_bytes == len(b"video-bytes")
    assert metadata.etag == "etag-1"
    assert client.delete_object("videos/user/video/original.mp4") is True
    assert client.get_blob_metadata("videos/user/video/original.mp4").exists is False
