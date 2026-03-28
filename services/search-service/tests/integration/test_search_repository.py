"""Integration tests for SearchRepository.

Uses testcontainers to spin up a real PostgreSQL instance with pgvector.
Tests: readiness gate, searchable corpus check, FTS search, ANN search, SOT gate.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.infra.db.models import Base, ChunkModel, VectorIndexEntryModel, VideoModel
from src.infra.db.search_repository import SearchRepository

# Testcontainers import is optional; skip all tests if unavailable
try:
    from testcontainers.postgres import PostgresContainer

    HAS_TESTCONTAINERS = True
except ImportError:
    HAS_TESTCONTAINERS = False

try:
    import docker

    docker.from_env().ping()
    HAS_DOCKER = True
except Exception:
    HAS_DOCKER = False

pytestmark = pytest.mark.skipif(
    not (HAS_TESTCONTAINERS and HAS_DOCKER),
    reason="testcontainers or Docker not available",
)

USER_A = uuid4()
USER_B = uuid4()


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield pg


@pytest.fixture(scope="module")
async def session_factory(pg_container):
    url = pg_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url, future=True)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture(scope="module")
def repo(session_factory) -> SearchRepository:
    return SearchRepository(session_factory)


async def _seed_video(
    session: AsyncSession, *, user_id, status="READY", title="Test Video"
):
    vid = uuid4()
    session.add(
        VideoModel(id=vid, user_id=user_id, title=title, status=status)
    )
    await session.flush()
    return vid


async def _seed_chunk(
    session: AsyncSession,
    *,
    video_id,
    text_val="sample text",
    enriched="enriched text",
    start_ms=0,
    end_ms=5000,
):
    cid = uuid4()
    session.add(
        ChunkModel(
            id=cid,
            video_id=video_id,
            chunk_index=0,
            text=text_val,
            enriched_text=enriched,
            start_ms=start_ms,
            end_ms=end_ms,
        )
    )
    await session.flush()
    return cid


async def _seed_vector(
    session: AsyncSession, *, chunk_id, user_id, video_id, embedding=None
):
    session.add(
        VectorIndexEntryModel(
            chunk_id=chunk_id,
            user_id=user_id,
            video_id=video_id,
            embedding_vector=embedding or [0.1, 0.2, 0.3],
            embedding_model_version="test-v001",
        )
    )
    await session.flush()


class TestCheckCorpusReadiness:
    async def test_user_with_no_videos(self, repo) -> None:
        result = await repo.check_corpus_readiness(uuid4())
        assert result.total_videos == 0
        assert result.non_ready_count == 0

    async def test_user_with_only_ready_videos(self, session_factory, repo) -> None:
        user = uuid4()
        async with session_factory() as session:
            await _seed_video(session, user_id=user, status="READY")
            await session.commit()

        result = await repo.check_corpus_readiness(user)
        assert result.total_videos == 1
        assert result.non_ready_count == 0

    async def test_user_with_pending_video(self, session_factory, repo) -> None:
        user = uuid4()
        async with session_factory() as session:
            await _seed_video(session, user_id=user, status="PENDING")
            await session.commit()

        result = await repo.check_corpus_readiness(user)
        assert result.total_videos == 1
        assert result.non_ready_count == 1

    async def test_mixed_ready_and_non_ready(
        self, session_factory, repo
    ) -> None:
        user = uuid4()
        async with session_factory() as session:
            await _seed_video(session, user_id=user, status="READY")
            await _seed_video(session, user_id=user, status="PROCESSING")
            await session.commit()

        result = await repo.check_corpus_readiness(user)
        assert result.total_videos == 2
        assert result.non_ready_count == 1

    async def test_other_user_not_visible(
        self, session_factory, repo
    ) -> None:
        owner = uuid4()
        other = uuid4()
        async with session_factory() as session:
            await _seed_video(session, user_id=owner, status="PROCESSING")
            await session.commit()

        result = await repo.check_corpus_readiness(other)
        assert result.total_videos == 0
        assert result.non_ready_count == 0


class TestFTSSearch:
    async def test_finds_matching_chunk(self, session_factory, repo) -> None:
        user = uuid4()
        async with session_factory() as session:
            vid = await _seed_video(session, user_id=user)
            await _seed_chunk(
                session,
                video_id=vid,
                text_val="machine learning tutorial",
                enriched="machine learning tutorial for beginners",
            )
            await session.commit()

        results = await repo.fts_search(user, "machine learning", top_k=10)
        assert len(results) >= 1

    async def test_no_match_returns_empty(self, session_factory, repo) -> None:
        user = uuid4()
        async with session_factory() as session:
            vid = await _seed_video(session, user_id=user)
            await _seed_chunk(session, video_id=vid, text_val="cooking recipe")
            await session.commit()

        results = await repo.fts_search(user, "quantum physics", top_k=10)
        assert len(results) == 0

    async def test_tenancy_enforced(self, session_factory, repo) -> None:
        owner = uuid4()
        other = uuid4()
        async with session_factory() as session:
            vid = await _seed_video(session, user_id=owner)
            await _seed_chunk(session, video_id=vid, text_val="secret data")
            await session.commit()

        results = await repo.fts_search(other, "secret data", top_k=10)
        assert len(results) == 0

    async def test_non_ready_video_excluded(self, session_factory, repo) -> None:
        user = uuid4()
        async with session_factory() as session:
            vid = await _seed_video(session, user_id=user, status="PROCESSING")
            await _seed_chunk(session, video_id=vid, text_val="test data")
            await session.commit()

        results = await repo.fts_search(user, "test data", top_k=10)
        assert len(results) == 0


class TestANNSearch:
    async def test_finds_nearest_chunk(self, session_factory, repo) -> None:
        user = uuid4()
        async with session_factory() as session:
            vid = await _seed_video(session, user_id=user)
            cid = await _seed_chunk(session, video_id=vid, text_val="vector test")
            await _seed_vector(
                session,
                chunk_id=cid,
                user_id=user,
                video_id=vid,
                embedding=[1.0, 0.0, 0.0],
            )
            await session.commit()

        results = await repo.ann_search(user, [1.0, 0.0, 0.0], top_k=10)
        assert len(results) >= 1
        assert results[0].chunk_id == cid

    async def test_tenancy_enforced(self, session_factory, repo) -> None:
        owner = uuid4()
        other = uuid4()
        async with session_factory() as session:
            vid = await _seed_video(session, user_id=owner)
            cid = await _seed_chunk(session, video_id=vid)
            await _seed_vector(
                session,
                chunk_id=cid,
                user_id=owner,
                video_id=vid,
                embedding=[0.5, 0.5, 0.0],
            )
            await session.commit()

        results = await repo.ann_search(other, [0.5, 0.5, 0.0], top_k=10)
        assert len(results) == 0

    async def test_ranking_by_distance(self, session_factory, repo) -> None:
        user = uuid4()
        async with session_factory() as session:
            vid = await _seed_video(session, user_id=user)
            close_cid = await _seed_chunk(
                session, video_id=vid, text_val="close"
            )
            far_cid = await _seed_chunk(
                session, video_id=vid, text_val="far"
            )
            await _seed_vector(
                session,
                chunk_id=close_cid,
                user_id=user,
                video_id=vid,
                embedding=[1.0, 0.0, 0.0],
            )
            await _seed_vector(
                session,
                chunk_id=far_cid,
                user_id=user,
                video_id=vid,
                embedding=[0.0, 1.0, 0.0],
            )
            await session.commit()

        results = await repo.ann_search(user, [1.0, 0.0, 0.0], top_k=10)
        assert len(results) == 2
        assert results[0].chunk_id == close_cid
        assert results[1].chunk_id == far_cid

    async def test_top_k_limits_results(self, session_factory, repo) -> None:
        user = uuid4()
        async with session_factory() as session:
            vid = await _seed_video(session, user_id=user)
            for i in range(5):
                cid = await _seed_chunk(
                    session, video_id=vid, text_val=f"chunk {i}"
                )
                await _seed_vector(
                    session,
                    chunk_id=cid,
                    user_id=user,
                    video_id=vid,
                    embedding=[float(i) / 5, 1.0 - float(i) / 5, 0.0],
                )
            await session.commit()

        results = await repo.ann_search(user, [0.0, 1.0, 0.0], top_k=3)
        assert len(results) == 3


class TestSOTGate:
    async def test_passes_ready_owned_chunks(self, session_factory, repo) -> None:
        user = uuid4()
        async with session_factory() as session:
            vid = await _seed_video(
                session, user_id=user, title="My Video"
            )
            cid = await _seed_chunk(
                session,
                video_id=vid,
                text_val="original",
                enriched="enriched version",
                start_ms=1000,
                end_ms=5000,
            )
            await session.commit()

        records = await repo.sot_gate(user, [cid])
        assert len(records) == 1
        assert records[0].chunk_id == cid
        assert records[0].title == "My Video"
        assert records[0].text == "original"
        assert records[0].enriched_text == "enriched version"
        assert records[0].start_ms == 1000
        assert records[0].end_ms == 5000

    async def test_filters_non_ready_video(self, session_factory, repo) -> None:
        user = uuid4()
        async with session_factory() as session:
            vid = await _seed_video(session, user_id=user, status="DELETING")
            cid = await _seed_chunk(session, video_id=vid)
            await session.commit()

        records = await repo.sot_gate(user, [cid])
        assert len(records) == 0

    async def test_filters_other_user_chunks(self, session_factory, repo) -> None:
        owner = uuid4()
        other = uuid4()
        async with session_factory() as session:
            vid = await _seed_video(session, user_id=owner)
            cid = await _seed_chunk(session, video_id=vid)
            await session.commit()

        records = await repo.sot_gate(other, [cid])
        assert len(records) == 0

    async def test_filters_hard_deleted_chunk(self, session_factory, repo) -> None:
        user = uuid4()
        non_existent_chunk = uuid4()
        records = await repo.sot_gate(user, [non_existent_chunk])
        assert len(records) == 0

    async def test_empty_chunk_ids_returns_empty(self, repo) -> None:
        records = await repo.sot_gate(uuid4(), [])
        assert records == []

    async def test_mixed_valid_and_invalid(self, session_factory, repo) -> None:
        user = uuid4()
        async with session_factory() as session:
            vid = await _seed_video(session, user_id=user)
            valid_cid = await _seed_chunk(session, video_id=vid)
            await session.commit()

        invalid_cid = uuid4()
        records = await repo.sot_gate(user, [valid_cid, invalid_cid])
        assert len(records) == 1
        assert records[0].chunk_id == valid_cid
