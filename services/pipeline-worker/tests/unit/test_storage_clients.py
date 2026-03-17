from __future__ import annotations

from pathlib import Path

import pytest

from adapters.storage.gcs_client import GCSStorageClient
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


class _FakeBlob:
    def __init__(self) -> None:
        self.uploaded: list[str] = []
        self.downloaded: list[str] = []
        self.deleted = False

    def download_to_filename(self, path: str) -> None:
        self.downloaded.append(path)
        Path(path).write_bytes(b"gcs")

    def upload_from_filename(self, path: str) -> None:
        self.uploaded.append(path)

    def delete(self) -> None:
        self.deleted = True


class _FakeBucket:
    def __init__(self) -> None:
        self._blob = _FakeBlob()

    def blob(self, storage_path: str) -> _FakeBlob:
        return self._blob


@pytest.mark.asyncio
async def test_gcs_storage_client_delegates_to_blob(tmp_path) -> None:
    bucket = _FakeBucket()
    client = GCSStorageClient(lambda: bucket)
    source = tmp_path / "source.txt"
    source.write_text("hello")

    await client.upload_object(source, "foo")
    await client.download_object("foo", tmp_path / "downloaded.txt")
    await client.delete_object("foo")

    assert bucket._blob.uploaded == [str(source)]
    assert bucket._blob.deleted is True
