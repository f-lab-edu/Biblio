from uuid import uuid4
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.infra.db.models import (
    Base,
    ProjectModel,
    VideoModel,
    ChunkModel,
    VectorIndexCatalogModel,
    VectorIndexEntryModel,
)
from src.release.video_reembed import VideoReembedService


class _StubEmbedder:
    def __init__(self, *, dimension: int = 2) -> None:
        self._dimension = dimension
        self.call_text_counts: list[int] = []

    async def embed_texts(self, texts, *, trace_id, model_version=None):
        self.call_text_counts.append(len(texts))
        rows = [[0.1] * self._dimension for _ in texts]

        class _R:
            embeddings = rows

        return _R()


async def _seed_ready_video_with_chunks(session, *, chunk_count: int):
    user_id = uuid4()
    project = ProjectModel(user_id=user_id, title="p", search_serving_state="ROLLBACK_EXCLUDED")
    session.add(project)
    await session.flush()
    video = VideoModel(user_id=user_id, project_id=project.id, status="READY", title="v")
    session.add(video)
    await session.flush()
    for i in range(chunk_count):
        session.add(
            ChunkModel(video_id=video.id, text=f"chunk-{i}", enriched_text=None, embedding_model_version="v2")
        )
    await session.flush()
    return video


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_reembed_video_writes_target_index_vectors(session):
    user_id = uuid4()
    project = ProjectModel(user_id=user_id, title="p", search_serving_state="ROLLBACK_EXCLUDED")
    session.add(project)
    await session.flush()
    video = VideoModel(user_id=user_id, project_id=project.id, status="READY", title="v")
    session.add(video)
    await session.flush()
    session.add(ChunkModel(video_id=video.id, text="hello", enriched_text=None, embedding_model_version="v2"))
    await session.flush()

    service = VideoReembedService(session=session, embedding_client=_StubEmbedder())
    count = await service.reembed_video(
        video_id=video.id, target_model_version="v1", target_index_name="index-v1", trace_id=uuid4(),
    )
    assert count == 1
    rows = (await session.execute(
        select(VectorIndexEntryModel).where(VectorIndexEntryModel.index_name == "index-v1")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].embedding_model_version == "v1"
    assert rows[0].video_id == video.id


@pytest.mark.asyncio
async def test_reembed_video_splits_chunks_into_batches(session):
    video = await _seed_ready_video_with_chunks(session, chunk_count=5)
    embedder = _StubEmbedder()

    service = VideoReembedService(session=session, embedding_client=embedder, batch_size=2)
    count = await service.reembed_video(
        video_id=video.id, target_model_version="v1", target_index_name="index-v1", trace_id=uuid4(),
    )

    assert count == 5
    # 5개 청크를 batch_size=2로 → 2,2,1 세 번 호출
    assert embedder.call_text_counts == [2, 2, 1]
    rows = (await session.execute(
        select(VectorIndexEntryModel).where(VectorIndexEntryModel.index_name == "index-v1")
    )).scalars().all()
    assert len(rows) == 5


@pytest.mark.asyncio
async def test_reembed_video_rejects_dimension_mismatch(session):
    video = await _seed_ready_video_with_chunks(session, chunk_count=2)
    session.add(
        VectorIndexCatalogModel(
            index_name="index-v1",
            model_version="v1",
            embedding_dimension=3,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    # 카탈로그 기대 차원=3 이지만 임베더가 2차원 벡터 반환
    embedder = _StubEmbedder(dimension=2)

    service = VideoReembedService(session=session, embedding_client=embedder, batch_size=8)
    with pytest.raises(ValueError, match="dimension mismatch"):
        await service.reembed_video(
            video_id=video.id, target_model_version="v1", target_index_name="index-v1", trace_id=uuid4(),
        )
