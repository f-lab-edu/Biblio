from pathlib import Path

import pytest

from src.infra.storage.gcs import GCSArtifactStore


class _FakeBlob:
    def __init__(self, name: str, objects: dict[str, bytes]) -> None:
        self.name = name
        self._objects = objects

    def download_to_filename(self, destination: str) -> None:
        Path(destination).write_bytes(self._objects[self.name])

    def upload_from_filename(self, source: str) -> None:
        self._objects[self.name] = Path(source).read_bytes()


class _FakeBucket:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(name, self._objects)


class _FakeClient:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects
        self.bucket_names: list[str] = []

    def bucket(self, name: str) -> _FakeBucket:
        self.bucket_names.append(name)
        return _FakeBucket(self._objects)

    def list_blobs(self, bucket_name: str, *, prefix: str):
        self.bucket_names.append(bucket_name)
        return [
            _FakeBlob(name, self._objects)
            for name in self._objects
            if name.startswith(prefix)
        ]


@pytest.mark.asyncio
async def test_gcs_artifact_store_lists_downloads_uploads_and_builds_uri(tmp_path) -> None:
    objects = {
        "feedback/raw_logs/schema_version=1/events-1.jsonl": b'{"event":1}\n',
        "feedback/error_logs/schema_version=1/events-1.jsonl": b'{"event":2}\n',
    }
    client = _FakeClient(objects)
    store = GCSArtifactStore(bucket_name="biblio-feedback-logs-dev-001", client=client)

    names = await store.list_objects("feedback/raw_logs")
    download_path = tmp_path / "downloaded.jsonl"
    upload_path = tmp_path / "upload.jsonl"
    upload_path.write_bytes(b'{"event":3}\n')

    await store.download_object("feedback/raw_logs/schema_version=1/events-1.jsonl", download_path)
    await store.upload_object(upload_path, "feedback/datasets/dataset-v1/train.jsonl")

    assert names == ["feedback/raw_logs/schema_version=1/events-1.jsonl"]
    assert download_path.read_bytes() == b'{"event":1}\n'
    assert objects["feedback/datasets/dataset-v1/train.jsonl"] == b'{"event":3}\n'
    assert store.object_uri("feedback/datasets/dataset-v1/train.jsonl") == (
        "gs://biblio-feedback-logs-dev-001/feedback/datasets/dataset-v1/train.jsonl"
    )
