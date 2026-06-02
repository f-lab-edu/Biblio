from src.bootstrap import _build_artifact_store
from src.config.settings import Settings
from src.infra.storage.gcs import GCSArtifactStore
from src.infra.storage.local import LocalArtifactStore

from tests.unit.test_settings import _required_env


def test_build_artifact_store_uses_local_backend_by_default(monkeypatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)

    store = _build_artifact_store(Settings())

    assert isinstance(store, LocalArtifactStore)


def test_build_artifact_store_uses_gcs_backend(monkeypatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ARTIFACT_STORE_BACKEND", "gcs")
    monkeypatch.setenv("GCS_FEEDBACK_LOG_BUCKET_NAME", "biblio-feedback-logs-dev-001")

    store = _build_artifact_store(Settings())

    assert isinstance(store, GCSArtifactStore)
    assert store.object_uri("feedback/raw_logs/example.jsonl") == (
        "gs://biblio-feedback-logs-dev-001/feedback/raw_logs/example.jsonl"
    )
