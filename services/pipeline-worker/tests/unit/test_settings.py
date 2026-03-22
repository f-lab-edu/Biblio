import pytest
from pydantic import ValidationError

from config.settings import Settings, get_settings


REQUIRED_ENV = {
    "BROKER_TYPE": "pgmq",
    "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/app",
    "GCP_PROJECT_ID": "biblio-dev",
    "GCS_VIDEO_BUCKET_NAME": "bucket-name",
    "EMBEDDING_API_URL": "https://localhost:8002/embed",
}


def _set_env(monkeypatch: pytest.MonkeyPatch, extra: dict[str, str] | None = None) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)

    if extra:
        for key, value in extra.items():
            monkeypatch.setenv(key, value)


def test_settings_requires_mandatory_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_loads_defaults_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    get_settings.cache_clear()

    settings = Settings(_env_file=None)

    assert settings.broker_type == "pgmq"
    assert settings.worker_concurrency == 4
    assert settings.chunk_overlap_sentences == 1
    assert settings.gcp_location == "us-central1"
    assert settings.vision_model == "gemini-3.1-flash-lite-preview"
    assert settings.vision_timeout_sec == 15
    assert settings.poll_interval_sec == pytest.approx(1.0)
    assert settings.stt_recognizer == ""
    assert settings.stt_model_version == ""
    assert settings.embedding_model_version == ""
    assert settings.embedding_timeout_sec == 10
    assert settings.embedding_batch_size == 16
    assert settings.chunk_max_tokens == 300


def test_settings_reads_stt_batch_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, {
        "STT_SUBMIT_TIMEOUT_SEC": "30",
        "STT_OPERATION_TIMEOUT_SEC": "900",
    })

    settings = Settings(_env_file=None)

    assert settings.stt_submit_timeout_sec == 30
    assert settings.stt_operation_timeout_sec == 900


def test_settings_stt_batch_timeouts_have_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.stt_submit_timeout_sec == 30
    assert settings.stt_operation_timeout_sec == 900
