import pytest

from adapters.storage.inmemory_storage import InMemoryStorageClient


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
