import pytest
from pydantic import ValidationError

from src.config.settings import Settings


def _required_env() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/biblio",
        "RAW_FEEDBACK_LOG_PREFIX": "feedback/raw",
        "DATASET_ARTIFACT_PREFIX": "feedback/datasets",
        "MODEL_ARTIFACT_PREFIX": "model_artifacts/candidates",
        "MODEL_VERSION_PREFIX": "bge-m3",
        "SERVING_MODEL_ARTIFACT_PREFIX": "models",
        "EVALUATION_ARTIFACT_PREFIX": "feedback/evaluations",
        "MANAGED_EMBEDDING_ENDPOINT_URL": "https://embedding.local",
        "LOCAL_TRAINING_MODEL_NAME": "BAAI/bge-small-en-v1.5",
        "EMBEDDING_DIMENSION": "384",
        "TRAINING_CONFIG_PATH": "configs/training/smoke.yaml",
        "EVALUATION_DATASET_REF": "gs://bucket/eval/smoke.jsonl",
        "ARTIFACT_STORE_BACKEND": "local",
        "GCS_FEEDBACK_LOG_BUCKET_NAME": "",
        "GCS_ML_ARTIFACT_BUCKET_NAME": "",
    }


def test_settings_load_required_feedback_loop_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://user:pass@localhost:5432/biblio"
    assert settings.raw_feedback_log_prefix == "feedback/raw"
    assert settings.model_artifact_prefix == "model_artifacts/candidates"
    assert settings.model_version_prefix == "bge-m3"
    assert settings.serving_model_artifact_prefix == "models"
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
    assert settings.candidate_deployment_max_attempts == 5
    assert settings.max_retries == 3
    assert settings.retry_backoff_sec == pytest.approx(1.0)
    assert settings.app_role == "scheduler"
    assert settings.feedback_dataset_queue_name == "feedback.dataset"
    assert settings.feedback_training_queue_name == "feedback.training"
    assert settings.feedback_rollback_queue_name == "feedback.rollback.high"
    assert settings.scheduler_tick_interval_sec == 60
    assert settings.dataset_generation_hour_kst == 3
    assert settings.dataset_generation_minute_kst == 0
    assert settings.training_request_weekday_kst == "mon"
    assert settings.training_request_hour_kst == 4
    assert settings.training_request_minute_kst == 0
    assert settings.worker_poll_interval_sec == pytest.approx(1.0)
    assert settings.artifact_store_backend == "local"
    assert settings.gcs_feedback_log_bucket_name is None
    assert settings.gcs_ml_artifact_bucket_name is None


def test_settings_load_gcs_artifact_store_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ARTIFACT_STORE_BACKEND", "gcs")
    monkeypatch.setenv("GCS_FEEDBACK_LOG_BUCKET_NAME", "biblio-feedback-logs-dev-001")
    monkeypatch.setenv("GCS_ML_ARTIFACT_BUCKET_NAME", "biblio-ml-artifacts-dev-001")

    settings = Settings()

    assert settings.artifact_store_backend == "gcs"
    assert settings.gcs_feedback_log_bucket_name == "biblio-feedback-logs-dev-001"
    assert settings.gcs_ml_artifact_bucket_name == "biblio-ml-artifacts-dev-001"


def test_settings_reject_gcs_backend_without_feedback_log_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ARTIFACT_STORE_BACKEND", "gcs")
    monkeypatch.setenv("GCS_FEEDBACK_LOG_BUCKET_NAME", "")
    monkeypatch.setenv("GCS_ML_ARTIFACT_BUCKET_NAME", "biblio-ml-artifacts-dev-001")

    with pytest.raises(ValidationError):
        Settings()


def test_settings_reject_gcs_backend_without_ml_artifact_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ARTIFACT_STORE_BACKEND", "gcs")
    monkeypatch.setenv("GCS_FEEDBACK_LOG_BUCKET_NAME", "biblio-feedback-logs-dev-001")
    monkeypatch.setenv("GCS_ML_ARTIFACT_BUCKET_NAME", "")

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


def test_settings_load_runtime_role_and_queue_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _required_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("APP_ROLE", "rollback-worker")
    monkeypatch.setenv("FEEDBACK_DATASET_QUEUE_NAME", "custom.dataset")
    monkeypatch.setenv("FEEDBACK_TRAINING_QUEUE_NAME", "custom.training")
    monkeypatch.setenv("FEEDBACK_ROLLBACK_QUEUE_NAME", "custom.rollback")
    monkeypatch.setenv("SCHEDULER_TICK_INTERVAL_SEC", "30")
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SEC", "0.25")

    settings = Settings()

    assert settings.app_role == "rollback-worker"
    assert settings.feedback_dataset_queue_name == "custom.dataset"
    assert settings.feedback_training_queue_name == "custom.training"
    assert settings.feedback_rollback_queue_name == "custom.rollback"
    assert settings.scheduler_tick_interval_sec == 30
    assert settings.worker_poll_interval_sec == pytest.approx(0.25)
