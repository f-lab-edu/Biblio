from datetime import UTC, datetime
from uuid import uuid4

import pytest

import src.bootstrap as bootstrap
from src.bootstrap import _build_artifact_store, _build_serving_target_reloader
from src.config.settings import Settings
from src.infra.storage.gcs import GCSArtifactStore
from src.infra.storage.local import LocalArtifactStore
from src.release.serving_reload import (
    NoopServingTargetReloader,
    SearchServiceServingTargetReloader,
)

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
    monkeypatch.setenv("GCS_ML_ARTIFACT_BUCKET_NAME", "biblio-ml-artifacts-dev-001")

    store = _build_artifact_store(Settings())

    assert isinstance(store, GCSArtifactStore)
    assert store.object_uri("feedback/datasets/example.jsonl") == (
        "gs://biblio-ml-artifacts-dev-001/feedback/datasets/example.jsonl"
    )


def test_build_raw_feedback_log_store_uses_gcs_feedback_log_bucket(monkeypatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ARTIFACT_STORE_BACKEND", "gcs")
    monkeypatch.setenv("GCS_FEEDBACK_LOG_BUCKET_NAME", "biblio-feedback-logs-dev-001")
    monkeypatch.setenv("GCS_ML_ARTIFACT_BUCKET_NAME", "biblio-ml-artifacts-dev-001")

    store = bootstrap._build_raw_feedback_log_store(Settings())

    assert isinstance(store, GCSArtifactStore)
    assert store.object_uri("feedback/raw_logs/example.jsonl") == (
        "gs://biblio-feedback-logs-dev-001/feedback/raw_logs/example.jsonl"
    )


@pytest.mark.asyncio
async def test_dataset_worker_passes_separate_raw_and_artifact_stores(monkeypatch, tmp_path) -> None:
    captured: dict[str, str] = {}

    class FakeRuntimeContext:
        # RuntimeContext.cleanup is awaited by bootstrap_dataset_worker.
        async def cleanup(self) -> None:
            return None

    class FakeSessionContext:
        # Session factory is used as an async context manager in the worker.
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    class FakeSessionFactory:
        def __call__(self) -> FakeSessionContext:
            return FakeSessionContext()

    class CapturingDatasetBatchService:
        def __init__(self, *, raw_feedback_log_store, artifact_store, chunk_text_snapshot) -> None:
            captured["raw_uri"] = raw_feedback_log_store.object_uri("feedback/raw_logs/example.jsonl")
            captured["artifact_uri"] = artifact_store.object_uri("feedback/datasets/example.jsonl")

        # DatasetBatchService.materialize_latest is awaited by the worker.
        async def materialize_latest(self, **kwargs) -> None:
            return None

    # _build_runtime_context is awaited by bootstrap_dataset_worker.
    async def build_runtime_context(settings):
        return FakeRuntimeContext(), object(), FakeSessionFactory()

    async def run_consumer(consumer, broker, queue_name, *, run_once, poll_interval_sec) -> None:
        await consumer.consume(
            {
                "message_type": "DATASET_GENERATION_REQUEST",
                "payload_version": "v1",
                "trace_id": str(uuid4()),
                "attempt": 1,
                "issued_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            }
        )

    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ARTIFACT_STORE_BACKEND", "gcs")
    monkeypatch.setenv("GCS_FEEDBACK_LOG_BUCKET_NAME", "biblio-feedback-logs-dev-001")
    monkeypatch.setenv("GCS_ML_ARTIFACT_BUCKET_NAME", "biblio-ml-artifacts-dev-001")
    monkeypatch.setattr(bootstrap, "_build_runtime_context", build_runtime_context)
    monkeypatch.setattr(bootstrap, "_run_consumer", run_consumer)
    monkeypatch.setattr(bootstrap, "_workspace_dir", lambda settings, role: tmp_path)
    monkeypatch.setattr(bootstrap, "DatasetBatchService", CapturingDatasetBatchService)

    await bootstrap.bootstrap_dataset_worker(Settings(), run_once=True)

    assert captured == {
        "raw_uri": "gs://biblio-feedback-logs-dev-001/feedback/raw_logs/example.jsonl",
        "artifact_uri": "gs://biblio-ml-artifacts-dev-001/feedback/datasets/example.jsonl",
    }


def test_build_serving_target_reloader_noop_when_url_unset(monkeypatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)

    reloader = _build_serving_target_reloader(Settings())

    assert isinstance(reloader, NoopServingTargetReloader)


def test_build_serving_target_reloader_calls_search_service_when_url_set(monkeypatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("SEARCH_SERVICE_URL", "http://search-service:8000")

    reloader = _build_serving_target_reloader(Settings())

    assert isinstance(reloader, SearchServiceServingTargetReloader)
    assert reloader.base_url == "http://search-service:8000"
