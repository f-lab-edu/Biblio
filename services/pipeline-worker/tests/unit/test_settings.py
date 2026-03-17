from typing import Any

import pytest
from pydantic import ValidationError

from config.settings import Settings, get_settings
from main import build_application


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
        Settings()


def test_settings_loads_defaults_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    get_settings.cache_clear()

    settings = Settings()

    assert settings.broker_type == "pgmq"
    assert settings.worker_concurrency == 4
    assert settings.chunk_overlap_sentences == 1


@pytest.mark.asyncio
async def test_build_application_reaches_consumer_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    get_settings.cache_clear()
    calls: list[tuple[str, Any]] = []

    def fake_consumer_bootstrap(settings: Settings) -> None:
        calls.append(("consumer_bootstrap", settings.broker_type))

    app = build_application(consumer_bootstrap=fake_consumer_bootstrap)

    await app.run()

    assert calls == [("consumer_bootstrap", "pgmq")]
