"""Bootstrap: wire concrete dependencies based on settings.

Assembles SearchRepository, EmbeddingClient, LLMAdapter
and registers them in the DependencyContainer.
"""

from src.core.config import Settings
from src.core.dependencies import DependencyContainer
from src.infra.db.session import create_engine, create_session_factory
from src.infra.embedding.client import EmbeddingClient
from src.infra.llm.base import LLMAdapter
from src.infra.llm.gemini_adapter import GeminiLLMAdapter
from src.infra.llm.mock_adapter import MockLLMAdapter


def _build_llm_adapter(settings: Settings) -> LLMAdapter:
    if settings.llm_provider == "gemini":
        if not settings.gcp_project_id or not settings.gemini_model_name:
            raise ValueError(
                "GCP_PROJECT_ID and GEMINI_MODEL_NAME are required when LLM_PROVIDER=gemini"
            )
        return GeminiLLMAdapter(
            project_id=settings.gcp_project_id,
            location=settings.gcp_location,
            model_name=settings.gemini_model_name,
            timeout_sec=settings.llm_timeout_sec,
            max_retries=settings.llm_max_retries,
            temperature=settings.llm_temperature,
            max_output_tokens=settings.llm_max_output_tokens,
        )
    if settings.llm_provider == "mock":
        return MockLLMAdapter()

    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")


def build_production_container(settings: Settings) -> DependencyContainer:
    """Build a fully wired DependencyContainer for production."""
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    embedding_client = EmbeddingClient(
        base_url=settings.embedding_api_url,
        timeout_sec=settings.embedding_timeout_sec,
        max_retries=settings.embedding_max_retries,
    )

    llm_adapter = _build_llm_adapter(settings)

    return DependencyContainer(
        settings=settings,
        db_engine=engine,
        db_session_factory=session_factory,
        embedding_client=embedding_client,
        llm_adapter=llm_adapter,
    )
