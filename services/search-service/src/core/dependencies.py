import inspect
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.core.config import Settings, get_settings
from src.infra.db.search_repository import SearchRepository
from src.infra.embedding.client import EmbeddingClient
from src.infra.llm.base import LLMAdapter
from src.services.search_orchestrator import SearchOrchestrator
from src.services.serving_targets import ServingSearchTargetProvider


@dataclass(slots=True)
class DependencyContainer:
    settings: Settings
    db_engine: AsyncEngine | None = None
    db_session_factory: async_sessionmaker[AsyncSession] | None = None
    embedding_client: EmbeddingClient | None = None
    llm_adapter: LLMAdapter | None = None
    serving_target_provider: ServingSearchTargetProvider | None = None

    async def aclose(self) -> None:
        if self.embedding_client is not None:
            await self.embedding_client.aclose()

        if self.llm_adapter is not None:
            adapter_aclose = getattr(self.llm_adapter, "aclose", None)
            if callable(adapter_aclose):
                result = adapter_aclose()
                if inspect.isawaitable(result):
                    await result

        if self.db_engine is not None:
            await self.db_engine.dispose()


def build_dependency_container(settings: Settings | None = None) -> DependencyContainer:
    """Lightweight container for tests. Use bootstrap.build_production_container for prod."""
    return DependencyContainer(settings=settings or get_settings())


def get_container(request: Request) -> DependencyContainer:
    return request.app.state.container


def get_settings_dependency(
    container: DependencyContainer = Depends(get_container),
) -> Settings:
    return container.settings


def get_db_session_factory(
    container: DependencyContainer = Depends(get_container),
) -> async_sessionmaker[AsyncSession]:
    if container.db_session_factory is None:
        raise RuntimeError("db_session_factory not initialized. Use bootstrap.")
    return container.db_session_factory


def get_embedding_client(
    container: DependencyContainer = Depends(get_container),
) -> EmbeddingClient:
    if container.embedding_client is None:
        raise RuntimeError("embedding_client not initialized. Use bootstrap.")
    return container.embedding_client


def get_llm_adapter(
    container: DependencyContainer = Depends(get_container),
) -> LLMAdapter:
    if container.llm_adapter is None:
        raise RuntimeError("llm_adapter not initialized. Use bootstrap.")
    return container.llm_adapter


def get_search_orchestrator(
    container: DependencyContainer = Depends(get_container),
) -> SearchOrchestrator:
    # Created per-request: SearchRepository/SearchOrchestrator are stateless
    # (hold only references to session_factory/clients), so instantiation is cheap.
    if container.db_session_factory is None:
        raise RuntimeError("db_session_factory not initialized. Use bootstrap.")
    if container.embedding_client is None:
        raise RuntimeError("embedding_client not initialized. Use bootstrap.")
    if container.llm_adapter is None:
        raise RuntimeError("llm_adapter not initialized. Use bootstrap.")

    repo = SearchRepository(container.db_session_factory)
    if container.serving_target_provider is None:
        container.serving_target_provider = ServingSearchTargetProvider(
            repo,
            ttl_sec=container.settings.search_target_cache_ttl_sec,
        )
    return SearchOrchestrator(
        repo=repo,
        serving_target_provider=container.serving_target_provider,
        embedding_client=container.embedding_client,
        llm_adapter=container.llm_adapter,
        search_top_k=container.settings.search_top_k,
        final_top_k=container.settings.final_top_k,
        rrf_k=container.settings.rrf_k,
        snapshot_ttl_hours=container.settings.search_snapshot_ttl_hours,
    )
