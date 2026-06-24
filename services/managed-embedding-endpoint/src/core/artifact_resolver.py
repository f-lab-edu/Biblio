import shutil
from pathlib import Path
from typing import Any, Protocol

from src.core.settings import Settings


class ModelArtifactMaterializer(Protocol):
    """Prepare a model version as a local loadable path."""

    def materialize(self, model_version: str) -> str:
        """Return a local path that the model loader can load."""
        ...


class ModelArtifactResolver:
    """Resolve model versions to loadable artifact refs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def resolve(self, model_version: str) -> str:
        if self._settings.model_artifact_root:
            return str(Path(self._settings.model_artifact_root) / model_version)

        artifact_path = self._settings.model_artifact_path
        if Path(artifact_path).name == model_version:
            return artifact_path

        return model_version


class LocalModelArtifactMaterializer:
    """Resolve already-local model artifacts."""

    def __init__(self, settings: Settings) -> None:
        self._resolver = ModelArtifactResolver(settings)

    def materialize(self, model_version: str) -> str:
        artifact_path = self._resolver.resolve(model_version)
        path = Path(artifact_path)
        if path.is_absolute() and not path.exists():
            raise FileNotFoundError(f"Local model artifact not found: {artifact_path}")
        return artifact_path


class GcsModelArtifactMaterializer:
    """Download GCS model artifacts into the local model cache."""

    def __init__(self, settings: Settings, storage_client: Any) -> None:
        self._settings = settings
        self._storage_client = storage_client

    def materialize(self, model_version: str) -> str:
        cache_path = Path(self._settings.local_model_cache_root) / model_version
        if cache_path.exists():
            return str(cache_path)

        bucket_name = self._settings.gcs_ml_artifact_bucket_name
        if not bucket_name:
            raise ValueError("GCS_ML_ARTIFACT_BUCKET_NAME is required for gcs backend.")

        prefix = self._gcs_prefix(model_version)
        tmp_path = cache_path.parent / f".{model_version}.tmp"
        self._download_prefix(bucket_name, prefix, tmp_path)
        tmp_path.replace(cache_path)
        return str(cache_path)

    def _download_prefix(self, bucket_name: str, prefix: str, tmp_path: Path) -> None:
        if tmp_path.exists():
            shutil.rmtree(tmp_path)
        tmp_path.mkdir(parents=True)

        downloaded_count = 0
        bucket = self._storage_client.bucket(bucket_name)
        try:
            for blob in bucket.list_blobs(prefix=prefix):
                relative_name = blob.name.removeprefix(prefix).lstrip("/")
                if not relative_name:
                    continue
                destination = tmp_path / relative_name
                destination.parent.mkdir(parents=True, exist_ok=True)
                blob.download_to_filename(str(destination))
                downloaded_count += 1

            if downloaded_count == 0:
                raise FileNotFoundError(
                    f"No model artifact files found under gs://{bucket_name}/{prefix}"
                )
        except Exception:
            shutil.rmtree(tmp_path, ignore_errors=True)
            raise

    def _gcs_prefix(self, model_version: str) -> str:
        artifact_prefix = self._settings.model_artifact_prefix.strip("/")
        if not artifact_prefix:
            return f"{model_version}/"
        return f"{artifact_prefix}/{model_version}/"


def build_model_artifact_materializer(settings: Settings) -> ModelArtifactMaterializer:
    backend = settings.model_artifact_backend.lower()
    if backend == "local":
        return LocalModelArtifactMaterializer(settings)
    if backend == "gcs":
        from google.cloud import storage

        return GcsModelArtifactMaterializer(settings, storage.Client())
    raise ValueError(
        f"Unsupported MODEL_ARTIFACT_BACKEND: {settings.model_artifact_backend}"
    )
