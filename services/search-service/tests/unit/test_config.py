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


class TestSettingsRequired:
    def test_all_required_present(self) -> None:
        settings = Settings(**_make_env())
        assert settings.jwt_secret_key == "test-secret"
        assert settings.database_url == "postgresql+asyncpg://u:p@localhost/db"
        assert settings.embedding_api_url == "http://localhost:8081/embed"

    @pytest.mark.parametrize("missing_key", ["JWT_SECRET_KEY", "DATABASE_URL", "EMBEDDING_API_URL"])
    def test_missing_required_raises(self, missing_key: str) -> None:
        env = _make_env()
        del env[missing_key]
        with pytest.raises(ValidationError):
            Settings(**env)


class TestSettingsDefaults:
    def test_override_defaults(self) -> None:
        settings = Settings(**_make_env(
            LLM_PROVIDER="mock",
            LLM_TEMPERATURE="0.5",
            LLM_MAX_OUTPUT_TOKENS="256",
            SEARCH_TOP_K="30",
            FINAL_TOP_K="10",
            RRF_K="80",
            SEARCH_SNAPSHOT_TTL_HOURS="12",
            SEARCH_TARGET_CACHE_TTL_SEC="30",
        ))
        assert settings.llm_provider == "mock"
        assert settings.llm_temperature == pytest.approx(0.5)
        assert settings.llm_max_output_tokens == 256
        assert settings.search_top_k == 30
        assert settings.final_top_k == 10
        assert settings.rrf_k == 80
        assert settings.search_snapshot_ttl_hours == 12
        assert settings.search_target_cache_ttl_sec == 30
