import pytest
from pydantic import ValidationError

from src.config.settings import Settings, get_settings


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
    assert settings.queue_visibility_timeout_sec == 1800
    assert settings.delete_queue_visibility_timeout_sec == 300
    assert settings.stale_processing_reclaim_sec == 1500
    assert settings.chunk_overlap_sentences == 1
    assert settings.stt_location == "us"
    assert settings.vision_location == "global"
    assert settings.vision_model == "gemini-3.1-flash-lite"
    assert settings.vision_timeout_sec == 15
    assert settings.poll_interval_sec == pytest.approx(1.0)
    assert settings.stt_recognizer == ""
    assert settings.stt_model_version == ""
    assert settings.embedding_model_version == ""
    assert settings.embedding_timeout_sec == 10
    assert settings.embedding_batch_size == 16
    assert settings.chunk_max_tokens == 300
    assert settings.download_timeout_sec == 600
    assert settings.max_audio_duration_sec == 3600
    assert settings.audio_part_duration_sec == 900
    assert settings.audio_part_overlap_sec == 5
    assert settings.stt_part_concurrency == 2
    assert settings.audio_processing_timeout_sec == 120
    assert settings.youtube_max_duration_sec == 3600
    assert settings.youtube_max_filesize_bytes == 500 * 1024 * 1024
    assert settings.youtube_max_height == 720


def test_settings_reads_independent_ai_locations(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, {
        "STT_LOCATION": "eu",
        "VISION_LOCATION": "europe-west4",
    })

    settings = Settings(_env_file=None)

    assert settings.stt_location == "eu"
    assert settings.vision_location == "europe-west4"


def test_settings_reads_vision_max_output_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, {"VISION_MAX_OUTPUT_TOKENS": "2048"})

    settings = Settings(_env_file=None)

    assert settings.vision_max_output_tokens == 2048


def test_settings_reads_long_audio_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, {
        "MAX_AUDIO_DURATION_SEC": "3500",
        "AUDIO_PART_DURATION_SEC": "800",
        "AUDIO_PART_OVERLAP_SEC": "4",
        "STT_PART_CONCURRENCY": "2",
        "AUDIO_PROCESSING_TIMEOUT_SEC": "300",
        "YOUTUBE_MAX_DURATION_SEC": "3500",
    })

    settings = Settings(_env_file=None)

    assert settings.max_audio_duration_sec == 3500
    assert settings.audio_part_duration_sec == 800
    assert settings.audio_part_overlap_sec == 4
    assert settings.stt_part_concurrency == 2
    assert settings.audio_processing_timeout_sec == 300
    assert settings.youtube_max_duration_sec == 3500


@pytest.mark.parametrize(
    "overrides",
    [
        {"STT_PART_CONCURRENCY": "3"},
        {"AUDIO_PART_DURATION_SEC": "5", "AUDIO_PART_OVERLAP_SEC": "5"},
        {"AUDIO_PART_DURATION_SEC": "1196", "AUDIO_PART_OVERLAP_SEC": "5"},
    ],
)
def test_settings_rejects_unsafe_long_audio_combinations(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str],
) -> None:
    _set_env(monkeypatch, overrides)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


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
