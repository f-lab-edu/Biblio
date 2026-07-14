import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.core.config import Settings
from src.core.dependencies import _build_db_session_factory


@pytest.mark.asyncio
async def test_pre_ping_replaces_terminated_pooled_connection(
    postgres_url: str,
) -> None:
    settings = Settings(
        _env_file=None,
        GCP_PROJECT_ID="test-project",
        GCS_VIDEO_BUCKET_NAME="test-bucket",
        JWT_SECRET_KEY="test-jwt-key",
        DATABASE_URL=postgres_url,
    )
    session_factory = _build_db_session_factory(settings)
    app_engine = session_factory.kw["bind"]
    assert isinstance(app_engine, AsyncEngine)

    admin_engine = create_async_engine(postgres_url)
    connection_terminated = asyncio.Event()

    try:
        async with session_factory() as session:
            terminated_backend_pid = await session.scalar(
                text("SELECT pg_backend_pid()")
            )

            app_connection = await session.connection()
            pooled_connection = await app_connection.get_raw_connection()
            driver_connection = pooled_connection.driver_connection
            driver_connection.add_termination_listener(
                lambda _: connection_terminated.set()
            )

        assert terminated_backend_pid is not None

        async with admin_engine.begin() as connection:
            terminated = await connection.scalar(
                text("SELECT pg_terminate_backend(:pid)"),
                {"pid": terminated_backend_pid},
            )

        assert terminated is True

        await asyncio.wait_for(
            connection_terminated.wait(),
            timeout=1.0,
        )

        async with session_factory() as session:
            replacement_backend_pid = await session.scalar(
                text("SELECT pg_backend_pid()")
            )

        assert replacement_backend_pid is not None
        assert replacement_backend_pid != terminated_backend_pid
    finally:
        await app_engine.dispose()
        await admin_engine.dispose()
