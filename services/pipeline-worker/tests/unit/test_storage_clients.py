import pytest

from src.infra.storage.gcs_client import GCSStorageClient
from src.infra.storage.inmemory_storage import InMemoryStorageClient


@pytest.mark.asyncio
async def test_inmemory_storage_download_upload_delete(tmp_path) -> None:
    storage = InMemoryStorageClient({"videos/source.mp4": b"video"})
    destination = tmp_path / "video.mp4"

    await storage.download_object("videos/source.mp4", destination)
    assert destination.read_bytes() == b"video"

    await storage.upload_object(destination, "videos/copied.mp4")
    assert storage.objects["videos/copied.mp4"] == b"video"

    await storage.delete_object("videos/copied.mp4")
    assert "videos/copied.mp4" not in storage.objects


async def test_inmemory_storage_builds_gs_uri() -> None:
    storage = InMemoryStorageClient(bucket_name="bucket")
    assert storage.object_uri("artifacts/video/audio.flac") == "gs://bucket/artifacts/video/audio.flac"


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeBatch:
    def __init__(self, client) -> None:
        self.client = client
        self.paths: list[str] = []
        self._responses: list[FakeResponse] = []

    def __enter__(self):
        self.client.active_batch = self
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.client.active_batch = None
        self.client.batches.append(self.paths)
        self._responses = [
            FakeResponse(self.client.statuses.get(path, 204))
            for path in self.paths
        ]


class FakeGCSClient:
    def __init__(self) -> None:
        self.active_batch: FakeBatch | None = None
        self.batches: list[list[str]] = []
        self.statuses: dict[str, int] = {}

    def batch(self, *, raise_exception: bool):
        assert raise_exception is False
        return FakeBatch(self)


class FakeBucket:
    def __init__(self, client: FakeGCSClient) -> None:
        self.client = client

    def blob(self, path: str):
        return FakeBlob(path, self.client)


class FakeBlob:
    def __init__(self, path: str, client: FakeGCSClient) -> None:
        self.path = path
        self.client = client

    def delete(self) -> None:
        assert self.client.active_batch is not None
        self.client.active_batch.paths.append(self.path)


@pytest.mark.asyncio
async def test_gcs_storage_batches_delete_objects_by_100() -> None:
    fake_client = FakeGCSClient()
    storage = GCSStorageClient(
        bucket_factory=lambda: FakeBucket(fake_client),
        bucket_name="bucket",
    )

    await storage.delete_objects([f"videos/{index}.mp4" for index in range(101)])

    assert [len(batch) for batch in fake_client.batches] == [100, 1]


@pytest.mark.asyncio
async def test_gcs_storage_ignores_missing_objects_and_raises_other_failures() -> None:
    fake_client = FakeGCSClient()
    fake_client.statuses = {
        "videos/missing.mp4": 404,
        "videos/fail.mp4": 500,
    }
    storage = GCSStorageClient(
        bucket_factory=lambda: FakeBucket(fake_client),
        bucket_name="bucket",
    )

    await storage.delete_objects(["videos/missing.mp4"])
    with pytest.raises(RuntimeError):
        await storage.delete_objects(["videos/fail.mp4"])
