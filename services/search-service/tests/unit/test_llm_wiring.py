"""Tests for LLM adapter wiring via bootstrap."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.bootstrap import _build_llm_adapter
from src.core.config import Settings
from src.infra.llm.gemini_adapter import GeminiLLMAdapter
from src.infra.llm.mock_adapter import MockLLMAdapter


def _make_settings(**overrides: object) -> Settings:
    defaults = {
        "JWT_SECRET_KEY": "test-secret",
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "EMBEDDING_API_URL": "http://localhost:8081/embed",
    }
    defaults.update(overrides)
    return Settings(**defaults)


class TestBuildLLMAdapter:
    def test_mock_provider(self) -> None:
        settings = _make_settings(LLM_PROVIDER="mock")
        adapter = _build_llm_adapter(settings)
        assert isinstance(adapter, MockLLMAdapter)

    def test_gemini_provider(self) -> None:
        settings = _make_settings(
            LLM_PROVIDER="gemini",
            GCP_PROJECT_ID="test-project",
            GEMINI_MODEL_NAME="gemini-2.0-flash",
            LLM_TEMPERATURE="0.4",
            LLM_MAX_OUTPUT_TOKENS="384",
        )
        with patch("src.bootstrap.GeminiLLMAdapter") as adapter_cls:
            adapter_cls.return_value = object()
            adapter = _build_llm_adapter(settings)

        assert adapter is adapter_cls.return_value
        adapter_cls.assert_called_once_with(
            project_id="test-project",
            location="us-central1",
            model_name="gemini-2.0-flash",
            timeout_sec=3,
            max_retries=1,
            temperature=0.4,
            max_output_tokens=384,
        )

    def test_gemini_without_project_id_raises(self) -> None:
        settings = _make_settings(
            LLM_PROVIDER="gemini",
            GCP_PROJECT_ID="",
            GEMINI_MODEL_NAME="gemini-2.0-flash",
        )
        with pytest.raises(ValueError, match="GCP_PROJECT_ID"):
            _build_llm_adapter(settings)

    def test_gemini_without_model_name_raises(self) -> None:
        settings = _make_settings(
            LLM_PROVIDER="gemini",
            GCP_PROJECT_ID="test-project",
            GEMINI_MODEL_NAME="",
        )
        with pytest.raises(ValueError, match="GEMINI_MODEL_NAME"):
            _build_llm_adapter(settings)

    def test_unsupported_provider_raises(self) -> None:
        # Need to bypass Literal validation for this test
        settings = _make_settings(LLM_PROVIDER="mock")
        # Manually override after construction
        object.__setattr__(settings, "llm_provider", "unsupported")
        with pytest.raises(ValueError, match="Unsupported"):
            _build_llm_adapter(settings)


class TestMockLLMAdapter:
    async def test_mock_returns_valid_structure(self) -> None:
        adapter = MockLLMAdapter()
        result = await adapter.generate("system", "test prompt", trace_id="trace-1")
        assert "<ANSWER>" in result.text
        assert "</ANSWER>" in result.text
        assert "<USED_REFS_JSON>" in result.text
        assert "used_refs" in result.text

    async def test_mock_custom_answer(self) -> None:
        adapter = MockLLMAdapter(answer="Custom answer [2].", used_refs=[2])
        result = await adapter.generate("system", "test", trace_id="trace-1")
        assert "Custom answer [2]." in result.text
        assert '{"used_refs":[2]}' in result.text

    async def test_mock_raises_configured_error(self) -> None:
        from src.infra.llm.base import LLMAdapterError

        error = LLMAdapterError(
            code="UNAVAILABLE", message="test error", retryable=True
        )
        adapter = MockLLMAdapter(error=error)
        with pytest.raises(LLMAdapterError, match="test error"):
            await adapter.generate("system", "test", trace_id="trace-1")


class TestGeminiLLMAdapter:
    async def test_generate_passes_generation_policy_to_sdk(self) -> None:
        fake_response = SimpleNamespace(text="<ANSWER>ok</ANSWER><USED_REFS_JSON>{\"used_refs\":[]}</USED_REFS_JSON>")
        fake_generate = AsyncMock(return_value=fake_response)
        fake_client = SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=fake_generate)),
            close=lambda: None,
        )

        with patch("src.infra.llm.gemini_adapter.genai.Client", return_value=fake_client):
            adapter = GeminiLLMAdapter(
                project_id="project",
                location="us-central1",
                model_name="gemini-2.0-flash",
                temperature=0.3,
                max_output_tokens=256,
            )

        result = await adapter.generate("system instruction", "prompt body", trace_id="trace-1")

        assert result.text == fake_response.text
        _, kwargs = fake_generate.await_args
        config = kwargs["config"]
        assert kwargs["model"] == "gemini-2.0-flash"
        assert kwargs["contents"] == "prompt body"
        assert config.system_instruction == "system instruction"
        assert config.temperature == 0.3
        assert config.max_output_tokens == 256
