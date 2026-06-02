import pytest
from pydantic import ValidationError

from src.config.settings import Settings


def _required_env() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/biblio",
        "RAW_FEEDBACK_LOG_PREFIX": "feedback/raw",
        "DATASET_ARTIFACT_PREFIX": "feedback/datasets",
        "MODEL_ARTIFACT_PREFIX": "model_artifacts/candidates",
        "EVALUATION_ARTIFACT_PREFIX": "feedback/evaluations",
        "MANAGED_EMBEDDING_ENDPOINT_URL": "https://embedding.local",
        "LOCAL_TRAINING_MODEL_NAME": "BAAI/bge-small-en-v1.5",
        "EMBEDDING_DIMENSION": "384",
        "TRAINING_CONFIG_PATH": "configs/training/smoke.yaml",
        "EVALUATION_DATASET_REF": "gs://bucket/eval/smoke.jsonl",
    }


def test_settings_load_required_feedback_loop_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://user:pass@localhost:5432/biblio"
    assert settings.raw_feedback_log_prefix == "feedback/raw"
    assert settings.model_artifact_prefix == "model_artifacts/candidates"
    assert settings.embedding_dimension == 384
    assert settings.worker_concurrency == 1
    assert settings.dataset_batch_size == 500
    assert settings.min_deduped_event_count == 1
    assert settings.min_training_group_count == 10
    assert settings.min_negative_count == 20
    assert settings.training_timeout_sec == 900
    assert settings.evaluation_timeout_sec == 300
    assert settings.rollback_restore_timeout_sec == 300
    assert settings.search_service_url == ""
    assert settings.stuck_run_timeout_sec == 3600
    assert settings.reconciliation_interval_sec == 60
    assert settings.legacy_reindex_scan_interval_sec == 60
    assert settings.legacy_reindex_batch_size == 8
    assert settings.legacy_reindex_per_run_video_limit == 100
    assert settings.legacy_reindex_throttle_sleep_ms == 0
    assert settings.max_retries == 3
    assert settings.retry_backoff_sec == pytest.approx(1.0)


def test_settings_reject_empty_artifact_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MODEL_ARTIFACT_PREFIX", "")

    with pytest.raises(ValidationError):
        Settings()


def test_settings_load_dataset_eligibility_threshold_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MIN_TRAINING_GROUP_COUNT", "15")
    monkeypatch.setenv("MIN_NEGATIVE_COUNT", "30")

    settings = Settings()

    assert settings.min_training_group_count == 15
    assert settings.min_negative_count == 30


def test_settings_load_legacy_reindex_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("LEGACY_REINDEX_SCAN_INTERVAL_SEC", "30")
    monkeypatch.setenv("LEGACY_REINDEX_BATCH_SIZE", "4")
    monkeypatch.setenv("LEGACY_REINDEX_PER_RUN_VIDEO_LIMIT", "12")
    monkeypatch.setenv("LEGACY_REINDEX_THROTTLE_SLEEP_MS", "0")

    settings = Settings()

    assert settings.legacy_reindex_scan_interval_sec == 30
    assert settings.legacy_reindex_batch_size == 4
    assert settings.legacy_reindex_per_run_video_limit == 12
    assert settings.legacy_reindex_throttle_sleep_ms == 0
