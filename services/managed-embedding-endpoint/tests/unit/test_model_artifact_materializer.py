from pathlib import Path

import pytest

from src.core.artifact_resolver import (
    GcsModelArtifactMaterializer,
    LocalModelArtifactMaterializer,
)
from src.core.settings import Settings


class TestLocalModelArtifactMaterializer:
    def test_returns_existing_version_path_under_artifact_root(self, tmp_path: Path):
        model_path = tmp_path / "models" / "active-v1"
        model_path.mkdir(parents=True)
        settings = Settings(
            MODEL_ARTIFACT_PATH=str(model_path),
            MODEL_ARTIFACT_ROOT=str(tmp_path / "models"),
        )
        materializer = LocalModelArtifactMaterializer(settings)

        assert materializer.materialize("active-v1") == str(model_path)

    def test_raises_when_local_path_is_missing(self, tmp_path: Path):
        settings = Settings(
            MODEL_ARTIFACT_PATH=str(tmp_path / "models" / "active-v1"),
            MODEL_ARTIFACT_ROOT=str(tmp_path / "models"),
        )
        materializer = LocalModelArtifactMaterializer(settings)

        with pytest.raises(FileNotFoundError, match="active-v1"):
            materializer.materialize("active-v1")

    def test_keeps_existing_model_artifact_path_match_behavior(self, tmp_path: Path):
        model_path = tmp_path / "models" / "active-v1"
        model_path.mkdir(parents=True)
        settings = Settings(MODEL_ARTIFACT_PATH=str(model_path))
        materializer = LocalModelArtifactMaterializer(settings)

        assert materializer.materialize("active-v1") == str(model_path)

    def test_returns_missing_relative_model_reference(self):
        settings = Settings(MODEL_ARTIFACT_PATH="active-v1")
        materializer = LocalModelArtifactMaterializer(settings)

        assert materializer.materialize("candidate-v1") == "candidate-v1"


class _FakeBlob:
    def __init__(self, name: str, downloads: list[tuple[str, str]]) -> None:
        self.name = name
        self._downloads = downloads

    def download_to_filename(self, filename: str) -> None:
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        Path(filename).write_text("model-data", encoding="utf-8")
        self._downloads.append((self.name, filename))


class _FakeBucket:
    def __init__(self, blob_names: list[str], downloads: list[tuple[str, str]]) -> None:
        self._blob_names = blob_names
        self._downloads = downloads

    def list_blobs(self, prefix: str):
        return [
            _FakeBlob(name, self._downloads)
            for name in self._blob_names
            if name.startswith(prefix)
        ]


class _FakeStorageClient:
    def __init__(self, blob_names: list[str]) -> None:
        self.downloads: list[tuple[str, str]] = []
        self._blob_names = blob_names

    def bucket(self, bucket_name: str):
        self.bucket_name = bucket_name
        return _FakeBucket(self._blob_names, self.downloads)


class TestGcsModelArtifactMaterializer:
    def test_returns_cached_path_without_downloading(self, tmp_path: Path):
        cache_path = tmp_path / "active-v1"
        cache_path.mkdir()
        settings = Settings(
            MODEL_ARTIFACT_PATH=str(cache_path),
            MODEL_ARTIFACT_BACKEND="gcs",
            GCS_ML_ARTIFACT_BUCKET_NAME="biblio-perf-ml-artifact",
            LOCAL_MODEL_CACHE_ROOT=str(tmp_path),
        )
        storage_client = _FakeStorageClient(["models/active-v1/config.json"])
        materializer = GcsModelArtifactMaterializer(settings, storage_client)

        assert materializer.materialize("active-v1") == str(cache_path)
        assert storage_client.downloads == []

    def test_downloads_prefix_to_temp_dir_then_returns_cache_path(self, tmp_path: Path):
        settings = Settings(
            MODEL_ARTIFACT_PATH=str(tmp_path / "active-v1"),
            MODEL_ARTIFACT_BACKEND="gcs",
            GCS_ML_ARTIFACT_BUCKET_NAME="biblio-perf-ml-artifact",
            MODEL_ARTIFACT_PREFIX="models",
            LOCAL_MODEL_CACHE_ROOT=str(tmp_path),
        )
        storage_client = _FakeStorageClient(
            [
                "models/active-v1/config.json",
                "models/active-v1/nested/tokenizer.json",
            ]
        )
        materializer = GcsModelArtifactMaterializer(settings, storage_client)

        result = materializer.materialize("active-v1")

        assert result == str(tmp_path / "active-v1")
        assert (tmp_path / "active-v1" / "config.json").read_text(
            encoding="utf-8"
        ) == "model-data"
        assert (tmp_path / "active-v1" / "nested" / "tokenizer.json").exists()
        assert not (tmp_path / ".active-v1.tmp").exists()

    def test_raises_when_gcs_prefix_has_no_files(self, tmp_path: Path):
        settings = Settings(
            MODEL_ARTIFACT_PATH=str(tmp_path / "active-v1"),
            MODEL_ARTIFACT_BACKEND="gcs",
            GCS_ML_ARTIFACT_BUCKET_NAME="biblio-perf-ml-artifact",
            LOCAL_MODEL_CACHE_ROOT=str(tmp_path),
        )
        materializer = GcsModelArtifactMaterializer(settings, _FakeStorageClient([]))

        with pytest.raises(FileNotFoundError, match="models/active-v1"):
            materializer.materialize("active-v1")
