"""Smoke test: verify bootstrap module can be imported and DSN conversion works.

Does NOT call create_production_bootstrap (requires real GCP credentials).
Tests the parts that are unit-testable without external services.
"""

import pytest

from src.bootstrap import ProductionContext, QUEUE_NAMES, _to_asyncpg_dsn


def test_to_asyncpg_dsn_converts_sqlalchemy_format() -> None:
    assert _to_asyncpg_dsn("postgresql+asyncpg://user:pass@host:5432/db") == "postgresql://user:pass@host:5432/db"


def test_to_asyncpg_dsn_preserves_plain_dsn() -> None:
    assert _to_asyncpg_dsn("postgresql://user:pass@host:5432/db") == "postgresql://user:pass@host:5432/db"


def test_to_asyncpg_dsn_handles_sqlite_passthrough() -> None:
    """Non-PostgreSQL DSNs pass through unchanged (e.g. test SQLite)."""
    assert _to_asyncpg_dsn("sqlite+aiosqlite:///:memory:") == "sqlite+aiosqlite:///:memory:"


def test_queue_names_match_message_types() -> None:
    from src.schemas.messages import MessageType

    expected = {mt.value for mt in MessageType}
    assert set(QUEUE_NAMES) == expected


@pytest.mark.asyncio
async def test_production_context_cleanup_closes_all_resources() -> None:
    class StubEngine:
        def __init__(self) -> None:
            self.closed = False

        async def dispose(self) -> None:
            self.closed = True

    class StubPool:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class StubCloser:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    engine = StubEngine()
    pool = StubPool()
    vision = StubCloser()
    embedding = StubCloser()

    ctx = ProductionContext(
        engine=engine,
        pgmq_pool=pool,
        closers=(vision, embedding),
    )

    await ctx.cleanup()

    assert engine.closed is True
    assert pool.closed is True
    assert vision.closed is True
    assert embedding.closed is True
