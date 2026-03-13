import pytest
from pydantic import ValidationError

from src.core.config import Settings, get_settings
from src.main import create_app

REQUIRED_ENV_VARS = (
    "GCP_PROJECT_ID",
    "GCS_VIDEO_BUCKET_NAME",
    "JWT_SECRET_KEY",
    "DATABASE_URL",
)


@pytest.fixture(autouse=True)
def reset_settings_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()

    for env_var in (*REQUIRED_ENV_VARS, "BROKER_TYPE"):
        monkeypatch.delenv(env_var, raising=False)

    yield

    get_settings.cache_clear()


def test_settings_require_mandatory_environment_variables() -> None:
    with pytest.raises(ValidationError):
        Settings()


def test_settings_load_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GCP_PROJECT_ID", "project-id")
    monkeypatch.setenv("GCS_VIDEO_BUCKET_NAME", "video-bucket")
    monkeypatch.setenv("JWT_SECRET_KEY", "super-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/app")

    settings = get_settings()

    assert settings.gcp_project_id == "project-id"
    assert settings.gcs_video_bucket_name == "video-bucket"
    assert settings.jwt_secret_key == "super-secret"
    assert settings.database_url == "postgresql+asyncpg://user:pass@localhost:5432/app"
    assert settings.broker_type == "pgmq"


def test_create_app_fails_without_required_settings() -> None:
    with pytest.raises(ValidationError):
        create_app()


def test_create_app_boots_with_valid_settings() -> None:
    settings = Settings(
        gcp_project_id="project-id",
        gcs_video_bucket_name="video-bucket",
        jwt_secret_key="super-secret",
        database_url="postgresql+asyncpg://user:pass@localhost:5432/app",
        broker_type="inmemory",
    )

    app = create_app(settings)
    registered_paths = {route.path for route in app.router.routes}

    assert app.title == "Biblio Core API"
    assert app.state.container.settings == settings
    assert "/api/v1/health" in registered_paths
