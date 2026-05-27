import pytest
from pydantic import ValidationError

from src.core.settings import Settings


class TestSettingsDefaults:
    """Verify guardrail defaults match SPEC §2.3."""

    def test_guardrail_defaults(self):
        settings = Settings(MODEL_ARTIFACT_PATH="BAAI/bge-m3")
        assert settings.max_texts_per_request == 32
        assert settings.max_text_length_chars == 4096
        assert settings.max_payload_bytes == 262144
        assert settings.max_concurrency == 1

    def test_port_default(self):
        settings = Settings(MODEL_ARTIFACT_PATH="BAAI/bge-m3")
        assert settings.port == 8000

    def test_model_cache_dir_default_empty(self):
        settings = Settings(MODEL_ARTIFACT_PATH="BAAI/bge-m3")
        assert settings.model_cache_dir == ""

    def test_model_artifact_root_default_empty(self):
        settings = Settings(MODEL_ARTIFACT_PATH="BAAI/bge-m3")
        assert settings.model_artifact_root == ""

    def test_database_url_default_empty(self):
        settings = Settings(MODEL_ARTIFACT_PATH="BAAI/bge-m3")
        assert settings.database_url == ""


class TestSettingsRequired:
    """Verify required fields cause validation failure when missing."""

    def test_missing_model_artifact_path_fails(self, monkeypatch):
        monkeypatch.delenv("MODEL_ARTIFACT_PATH", raising=False)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_empty_model_artifact_path_fails(self):
        with pytest.raises(ValidationError):
            Settings(MODEL_ARTIFACT_PATH="")


class TestSettingsOverrides:
    """Verify env var overrides are applied."""

    def test_custom_guardrails(self):
        settings = Settings(
            MODEL_ARTIFACT_PATH="/models/bge-m3",
            MAX_TEXTS_PER_REQUEST=16,
            MAX_TEXT_LENGTH_CHARS=2048,
            MAX_PAYLOAD_BYTES=131072,
            MAX_CONCURRENCY=4,
        )
        assert settings.max_texts_per_request == 16
        assert settings.max_text_length_chars == 2048
        assert settings.max_payload_bytes == 131072
        assert settings.max_concurrency == 4

    def test_custom_port(self):
        settings = Settings(MODEL_ARTIFACT_PATH="BAAI/bge-m3", PORT=9000)
        assert settings.port == 9000

    def test_custom_release_reload_settings(self):
        settings = Settings(
            MODEL_ARTIFACT_PATH="/models/bge-m3-20260526T143000KST",
            MODEL_ARTIFACT_ROOT="/models",
            DATABASE_URL="postgresql+asyncpg://user:pass@db/app",
        )
        assert settings.model_artifact_root == "/models"
        assert settings.database_url == "postgresql+asyncpg://user:pass@db/app"
