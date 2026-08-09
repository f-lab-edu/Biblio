import pytest
from pydantic import ValidationError

from src.core.settings import Settings


def _settings(**overrides):
    return Settings(_env_file=None, **overrides)


class TestSettingsDefaults:
    """Verify guardrail defaults match SPEC §2.3."""

    def test_guardrail_defaults(self):
        settings = _settings(MODEL_ARTIFACT_PATH="BAAI/bge-m3")
        assert settings.max_texts_per_request == 32
        assert settings.max_text_length_chars == 4096
        assert settings.max_payload_bytes == 262144
        assert settings.max_concurrency == 1
        assert settings.inference_threads is None
        assert settings.search_request_limit == 32
        assert settings.video_preprocess_request_limit == 4
        assert settings.search_wait_timeout_sec == 5.0
        assert settings.video_preprocess_wait_timeout_sec == 20.0

    def test_port_default(self):
        settings = _settings(MODEL_ARTIFACT_PATH="BAAI/bge-m3")
        assert settings.port == 8000

    def test_model_cache_dir_default_empty(self):
        settings = _settings(MODEL_ARTIFACT_PATH="BAAI/bge-m3")
        assert settings.model_cache_dir == ""

    def test_model_artifact_root_default_empty(self):
        settings = _settings(MODEL_ARTIFACT_PATH="BAAI/bge-m3")
        assert settings.model_artifact_root == ""

    def test_database_url_default_empty(self):
        settings = _settings(MODEL_ARTIFACT_PATH="BAAI/bge-m3")
        assert settings.database_url == ""

    def test_model_artifact_materializer_defaults(self):
        settings = _settings(MODEL_ARTIFACT_PATH="BAAI/bge-m3")
        assert settings.model_artifact_backend == "local"
        assert settings.gcs_ml_artifact_bucket_name == ""
        assert settings.model_artifact_prefix == "models"
        assert settings.local_model_cache_root == "/models"


class TestSettingsRequired:
    """Verify required fields cause validation failure when missing."""

    def test_missing_model_artifact_path_fails(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACT_PATH", raising=False)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_empty_model_artifact_path_fails(self):
        with pytest.raises(ValidationError):
            _settings(MODEL_ARTIFACT_PATH="")


class TestSettingsOverrides:
    """Verify env var overrides are applied."""

    def test_custom_guardrails(self):
        settings = _settings(
            MODEL_ARTIFACT_PATH="/models/bge-m3",
            MAX_TEXTS_PER_REQUEST=16,
            MAX_TEXT_LENGTH_CHARS=2048,
            MAX_PAYLOAD_BYTES=131072,
            MAX_CONCURRENCY=4,
            INFERENCE_THREADS="2",
            SEARCH_REQUEST_LIMIT=12,
            VIDEO_PREPROCESS_REQUEST_LIMIT=3,
            SEARCH_WAIT_TIMEOUT_SEC=7,
            VIDEO_PREPROCESS_WAIT_TIMEOUT_SEC=25,
        )
        assert settings.max_texts_per_request == 16
        assert settings.max_text_length_chars == 2048
        assert settings.max_payload_bytes == 131072
        assert settings.max_concurrency == 4
        assert settings.inference_threads == 2
        assert settings.search_request_limit == 12
        assert settings.video_preprocess_request_limit == 3
        assert settings.search_wait_timeout_sec == 7
        assert settings.video_preprocess_wait_timeout_sec == 25

    @pytest.mark.parametrize("thread_count", [0, -1])
    def test_inference_threads_must_be_positive(self, thread_count):
        with pytest.raises(ValidationError):
            _settings(
                MODEL_ARTIFACT_PATH="BAAI/bge-m3",
                INFERENCE_THREADS=thread_count,
            )

    def test_custom_port(self):
        settings = _settings(MODEL_ARTIFACT_PATH="BAAI/bge-m3", PORT=9000)
        assert settings.port == 9000

    def test_custom_release_reload_settings(self):
        settings = _settings(
            MODEL_ARTIFACT_PATH="/models/bge-m3-20260526T143000KST",
            MODEL_ARTIFACT_ROOT="/models",
            DATABASE_URL="postgresql+asyncpg://user:pass@db/app",
        )
        assert settings.model_artifact_root == "/models"
        assert settings.database_url == "postgresql+asyncpg://user:pass@db/app"

    def test_custom_model_artifact_materializer_settings(self):
        settings = _settings(
            MODEL_ARTIFACT_PATH="/models/bge-m3-20260526T143000KST",
            MODEL_ARTIFACT_BACKEND="gcs",
            GCS_ML_ARTIFACT_BUCKET_NAME="biblio-perf-ml-artifact",
            MODEL_ARTIFACT_PREFIX="custom-models",
            LOCAL_MODEL_CACHE_ROOT="/cache",
        )
        assert settings.model_artifact_backend == "gcs"
        assert settings.gcs_ml_artifact_bucket_name == "biblio-perf-ml-artifact"
        assert settings.model_artifact_prefix == "custom-models"
        assert settings.local_model_cache_root == "/cache"
