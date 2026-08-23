"""Smoke test: verify bootstrap module can be imported and DSN conversion works.

Does NOT call create_production_bootstrap (requires real GCP credentials).
Tests the parts that are unit-testable without external services.
"""

import pytest

from src.bootstrap import (
    CONSUMER_QUEUE_NAMES,
    ProductionContext,
    QUEUE_NAMES,
    _queue_visibility_timeouts,
    _to_asyncpg_dsn,
    _validate_recovery_timeouts,
)
from src.config.settings import Settings


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


def test_consumer_reads_only_queues_with_registered_handlers() -> None:
    assert CONSUMER_QUEUE_NAMES == [
        "PREPROCESS_REQUEST",
        "NORMALIZE_VIDEO",
        "DELETE_REQUEST",
        "PROJECT_DELETE_REQUEST",
    ]


def test_queue_visibility_timeouts_are_stage_specific() -> None:
    settings = Settings(
        _env_file=None,
        BROKER_TYPE="pgmq",
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/app",
        GCP_PROJECT_ID="biblio-dev",
        GCS_VIDEO_BUCKET_NAME="bucket-name",
        EMBEDDING_API_URL="https://embedding.local/embed",
        QUEUE_VISIBILITY_TIMEOUT_SEC=1800,
        NORMALIZATION_QUEUE_VISIBILITY_TIMEOUT_SEC=7200,
        TRANSCRIPTION_QUEUE_VISIBILITY_TIMEOUT_SEC=4200,
        ENRICHMENT_QUEUE_VISIBILITY_TIMEOUT_SEC=120,
        EMBEDDING_QUEUE_VISIBILITY_TIMEOUT_SEC=300,
        DELETE_QUEUE_VISIBILITY_TIMEOUT_SEC=300,
    )

    assert _queue_visibility_timeouts(settings) == {
        "PREPROCESS_REQUEST": 1800,
        "NORMALIZE_VIDEO": 7200,
        "TRANSCRIBE_PART": 4200,
        "ENRICH_CHUNK": 120,
        "EMBED_BATCH": 300,
        "DELETE_REQUEST": 300,
        "PROJECT_DELETE_REQUEST": 300,
    }


def test_recovery_timeout_validation_rejects_stale_equal_to_vt() -> None:
    with pytest.raises(
        ValueError,
        match="STALE_PROCESSING_RECLAIM_SEC must be less than QUEUE_VISIBILITY_TIMEOUT_SEC",
    ):
        _validate_recovery_timeouts(
            stale_processing_reclaim_sec=1800,
            queue_visibility_timeout_sec=1800,
        )


def test_recovery_timeout_validation_accepts_stale_less_than_vt() -> None:
    _validate_recovery_timeouts(
        stale_processing_reclaim_sec=1500,
        queue_visibility_timeout_sec=1800,
    )


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
