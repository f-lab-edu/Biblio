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
        self.copy_operations: list[tuple[str, str]] = []

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(name, self._objects)

    def copy_blob(self, source_blob: _FakeBlob, destination_bucket: "_FakeBucket", new_name: str) -> _FakeBlob:
        destination_bucket._objects[new_name] = self._objects[source_blob.name]
        self.copy_operations.append((source_blob.name, new_name))
        return destination_bucket.blob(new_name)


class _FakeClient:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects
        self.bucket_names: list[str] = []
        self.bucket_instance = _FakeBucket(objects)

    def bucket(self, name: str) -> _FakeBucket:
        self.bucket_names.append(name)
        return self.bucket_instance

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


@pytest.mark.asyncio
async def test_gcs_artifact_store_copy_prefix_preserves_relative_paths() -> None:
    objects = {
        "models/active/config.json": b"config",
        "models/active/nested/model.bin": b"model",
        "models/active-extra/ignored.txt": b"ignored",
    }
    client = _FakeClient(objects)
    store = GCSArtifactStore(bucket_name="biblio-ml-artifacts", client=client)

    await store.copy_prefix("models/active", "models/candidate")

    assert objects["models/candidate/config.json"] == b"config"
    assert objects["models/candidate/nested/model.bin"] == b"model"
    assert "models/candidate/ignored.txt" not in objects
    assert client.bucket_instance.copy_operations == [
        ("models/active/config.json", "models/candidate/config.json"),
        ("models/active/nested/model.bin", "models/candidate/nested/model.bin"),
    ]


@pytest.mark.asyncio
async def test_gcs_artifact_store_copy_prefix_fails_when_source_is_empty() -> None:
    store = GCSArtifactStore(bucket_name="biblio-ml-artifacts", client=_FakeClient({}))

    with pytest.raises(FileNotFoundError):
        await store.copy_prefix("models/missing/", "models/candidate/")


@pytest.mark.asyncio
async def test_gcs_artifact_store_copy_prefix_fails_when_target_exists() -> None:
    objects = {
        "models/active/config.json": b"config",
        "models/candidate/config.json": b"existing",
    }
    store = GCSArtifactStore(bucket_name="biblio-ml-artifacts", client=_FakeClient(objects))

    with pytest.raises(FileExistsError):
        await store.copy_prefix("models/active/", "models/candidate/")

    assert objects["models/candidate/config.json"] == b"existing"
