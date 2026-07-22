import pytest
from pydantic import ValidationError

from src.core.config import Settings


def _make_env(**overrides: str) -> dict[str, str]:
    defaults = {
        "JWT_SECRET_KEY": "test-secret",
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "EMBEDDING_API_URL": "http://localhost:8081/embed",
    }
    defaults.update(overrides)
    return defaults


def _build_settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)


class TestSettingsRequired:
    def test_all_required_present(self) -> None:
        settings = _build_settings(**_make_env())
        assert settings.jwt_secret_key == "test-secret"
        assert settings.database_url == "postgresql+asyncpg://u:p@localhost/db"
        assert settings.embedding_api_url == "http://localhost:8081/embed"

    @pytest.mark.parametrize("missing_key", ["JWT_SECRET_KEY", "DATABASE_URL", "EMBEDDING_API_URL"])
    def test_missing_required_raises(self, missing_key: str) -> None:
        env = _make_env()
        del env[missing_key]
        with pytest.raises(ValidationError):
            _build_settings(**env)


class TestSettingsDefaults:
    def test_embedding_timeout_allows_server_queue_wait(self) -> None:
        settings = _build_settings(**_make_env())

        assert settings.embedding_timeout_sec == 15

    def test_override_defaults(self) -> None:
        settings = _build_settings(**_make_env(
            LLM_PROVIDER="mock",
            LLM_TEMPERATURE="0.5",
            LLM_MAX_OUTPUT_TOKENS="256",
            SEARCH_TOP_K="30",
            FINAL_TOP_K="10",
            RRF_K="80",
            SEARCH_SNAPSHOT_TTL_HOURS="12",
        ))
        assert settings.llm_provider == "mock"
        assert settings.llm_temperature == pytest.approx(0.5)
        assert settings.llm_max_output_tokens == 256
        assert settings.search_top_k == 30
        assert settings.final_top_k == 10
        assert settings.rrf_k == 80
        assert settings.search_snapshot_ttl_hours == 12
