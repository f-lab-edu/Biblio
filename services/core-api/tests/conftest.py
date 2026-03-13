from __future__ import annotations

import os
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from docker.errors import DockerException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.core.config import Settings
from src.infra.inmemory_broker import InMemoryBrokerClient
from src.infra.inmemory_storage import InMemoryStorageClient
from src.main import create_app
from tests.support import AppContext, SessionFactory, TEST_JWT_SIGNING_KEY


def to_asyncpg_url(connection_url: str) -> str:
    if connection_url.startswith("postgresql+psycopg2://"):
        return connection_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgresql://"):
        return connection_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return connection_url


@pytest.fixture(scope="session")
def postgres_url() -> Generator[str, None, None]:
    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except DockerException as exc:
        pytest.skip(f"Docker is required for integration-style tests: {exc}")

    try:
        yield to_asyncpg_url(container.get_connection_url())
    finally:
        container.stop()


@pytest.fixture(scope="session")
def migrated_database(postgres_url: str) -> Generator[None, None, None]:
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = postgres_url

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)
    command.upgrade(config, "head")

    try:
        yield
    finally:
        command.downgrade(config, "base")
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url


@pytest_asyncio.fixture
async def session_factory(
    postgres_url: str,
    migrated_database: None,
) -> AsyncGenerator[SessionFactory, None]:
    engine = create_async_engine(postgres_url, future=True)

    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE video"))

    factory: SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_context(
    postgres_url: str,
    session_factory: SessionFactory,
) -> AsyncGenerator[AppContext, None]:
    settings = Settings(
        gcp_project_id="project-id",
        gcs_video_bucket_name="video-bucket",
        jwt_secret_key=TEST_JWT_SIGNING_KEY,
        database_url=postgres_url,
        broker_type="inmemory",
    )
    app = create_app(settings)
    app.state.container.db_session_factory = session_factory
    app.state.container.storage_client = InMemoryStorageClient()
    app.state.container.broker_client = InMemoryBrokerClient()

    yield AppContext(app=app, settings=settings, session_factory=session_factory)


@pytest_asyncio.fixture
async def api_client(app_context: AppContext) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app_context.app),
        base_url="https://testserver",
    ) as client:
        yield client

