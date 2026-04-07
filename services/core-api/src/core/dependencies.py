from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import Settings, get_settings
from src.infra.gcs_client import GCSStorageClient
from src.infra.inmemory_broker import InMemoryBrokerClient
from src.infra.pgmq_client import PGMQBrokerClient


@dataclass(slots=True)
class DependencyContainer:
    settings: Settings
    db_session_factory: Any | None = None
    storage_client: Any | None = None
    broker_client: Any | None = None


def build_dependency_container(settings: Settings | None = None) -> DependencyContainer:
    return DependencyContainer(settings=settings or get_settings())


def _build_db_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(settings.database_url, future=True)
    return async_sessionmaker(engine, expire_on_commit=False)


def _build_storage_client(settings: Settings) -> GCSStorageClient:
    return GCSStorageClient(
        bucket_name=settings.gcs_video_bucket_name,
        project_id=settings.gcp_project_id,
    )


def _to_asyncpg_dsn(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return database_url


def _build_broker_client(settings: Settings) -> Any:
    if settings.broker_type == "inmemory":
        return InMemoryBrokerClient()
    return PGMQBrokerClient(dsn=_to_asyncpg_dsn(settings.database_url))


def get_container(request: Request) -> DependencyContainer:
    return request.app.state.container


def get_settings_dependency(
    container: DependencyContainer = Depends(get_container),
) -> Settings:
    return container.settings


def get_db_session_factory(
    container: DependencyContainer = Depends(get_container),
) -> Any | None:
    if container.db_session_factory is None:
        container.db_session_factory = _build_db_session_factory(container.settings)
    return container.db_session_factory


def get_storage_client(container: DependencyContainer = Depends(get_container)) -> Any | None:
    if container.storage_client is None:
        container.storage_client = _build_storage_client(container.settings)
    return container.storage_client


def get_broker_client(container: DependencyContainer = Depends(get_container)) -> Any | None:
    if container.broker_client is None:
        container.broker_client = _build_broker_client(container.settings)
    return container.broker_client
