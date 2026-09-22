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


@pytest.mark.asyncio
async def test_inmemory_storage_upload_if_absent_does_not_overwrite(tmp_path) -> None:
    storage = InMemoryStorageClient({"results/part.json": b"first"})
    source = tmp_path / "result.json"
    source.write_bytes(b"second")

    created = await storage.upload_object_if_absent(source, "results/part.json")

    assert created is False
    assert storage.objects["results/part.json"] == b"first"


async def test_inmemory_storage_builds_gs_uri() -> None:
    storage = InMemoryStorageClient(bucket_name="bucket")
    assert storage.object_uri("artifacts/video/audio.flac") == "gs://bucket/artifacts/video/audio.flac"


def test_inmemory_storage_creates_generation_bound_media_input() -> None:
    storage = InMemoryStorageClient({"videos/source.mp4": b"abcdef"})

    media_input = storage.create_media_input(
        "videos/source.mp4",
        expires_in_seconds=8100,
    )

    assert media_input.generation == "1"
    assert media_input.url == "https://storage.test/test-bucket/videos/source.mp4"


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
        self.uploaded_paths: set[str] = set()
        self._credentials = None

    def batch(self, *, raise_exception: bool):
        assert raise_exception is False
        return FakeBatch(self)


class FakeBucket:
    def __init__(self, client: FakeGCSClient) -> None:
        self.client = client
        self.blobs: list["FakeBlob"] = []
        self.listed_paths: list[str] = []

    def blob(self, path: str):
        blob = FakeBlob(path, self.client)
        self.blobs.append(blob)
        return blob

    def list_blobs(self, *, prefix: str):
        return [FakeBlob(path, self.client) for path in self.listed_paths if path.startswith(prefix)]


class FakeBlob:
    def __init__(self, path: str, client: FakeGCSClient) -> None:
        self.path = path
        self.name = path
        self.client = client
        self.generation = "123"
        self.content_encoding = None
        self.signed_url_kwargs: dict[str, object] | None = None

    def delete(self) -> None:
        assert self.client.active_batch is not None
        self.client.active_batch.paths.append(self.path)

    def reload(self) -> None:
        return None

    def exists(self) -> bool:
        return self.client.statuses.get(self.path, 200) != 404

    def generate_signed_url(self, **kwargs) -> str:
        self.signed_url_kwargs = kwargs
        return "https://storage.test/signed-source"

    def upload_from_filename(
        self,
        source: str,
        *,
        if_generation_match: int | None = None,
    ) -> None:
        del source
        if if_generation_match == 0 and self.path in self.client.uploaded_paths:
            from google.api_core.exceptions import PreconditionFailed

            raise PreconditionFailed("already exists")
        self.client.uploaded_paths.add(self.path)


def test_gcs_storage_creates_generation_bound_signed_url() -> None:
    fake_client = FakeGCSClient()
    bucket = FakeBucket(fake_client)
    storage = GCSStorageClient(
        bucket_factory=lambda: bucket,
        bucket_name="bucket",
    )

    media_input = storage.create_media_input(
        "videos/source.mp4",
        expires_in_seconds=8100,
    )

    assert media_input.url == "https://storage.test/signed-source"
    assert media_input.generation == "123"
    assert bucket.blobs[0].signed_url_kwargs is not None
    assert bucket.blobs[0].signed_url_kwargs["method"] == "GET"
    assert bucket.blobs[0].signed_url_kwargs["query_parameters"] == {
        "generation": "123"
    }


def test_gcs_storage_rejects_changed_generation() -> None:
    fake_client = FakeGCSClient()
    storage = GCSStorageClient(
        bucket_factory=lambda: FakeBucket(fake_client),
        bucket_name="bucket",
    )

    with pytest.raises(RuntimeError, match="source generation changed"):
        storage.create_media_input(
            "videos/source.mp4",
            expires_in_seconds=8100,
            expected_generation="older",
        )


@pytest.mark.asyncio
async def test_gcs_storage_checks_object_existence() -> None:
    fake_client = FakeGCSClient()
    fake_client.statuses["results/missing.json"] = 404
    storage = GCSStorageClient(
        bucket_factory=lambda: FakeBucket(fake_client),
        bucket_name="bucket",
    )

    assert await storage.object_exists("results/present.json") is True
    assert await storage.object_exists("results/missing.json") is False


@pytest.mark.asyncio
async def test_gcs_storage_lists_objects_by_prefix() -> None:
    fake_client = FakeGCSClient()
    bucket = FakeBucket(fake_client)
    bucket.listed_paths = [
        "artifacts/video/pipeline-runs/run-1/part.flac",
        "artifacts/other/pipeline-runs/run-2/part.flac",
    ]
    storage = GCSStorageClient(
        bucket_factory=lambda: bucket,
        bucket_name="bucket",
    )

    paths = await storage.list_objects("artifacts/video/pipeline-runs/")

    assert paths == ["artifacts/video/pipeline-runs/run-1/part.flac"]


@pytest.mark.asyncio
async def test_gcs_storage_upload_if_absent_uses_generation_precondition(
    tmp_path,
) -> None:
    fake_client = FakeGCSClient()
    storage = GCSStorageClient(
        bucket_factory=lambda: FakeBucket(fake_client),
        bucket_name="bucket",
    )
    source = tmp_path / "result.json"
    source.write_bytes(b"result")

    first = await storage.upload_object_if_absent(source, "results/part.json")
    second = await storage.upload_object_if_absent(source, "results/part.json")

    assert first is True
    assert second is False


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
