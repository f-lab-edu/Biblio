from uuid import uuid4
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.infra.db.models import Base, ProjectModel, VideoModel, ChunkModel, VectorIndexEntryModel
from src.release.video_reembed import VideoReembedService


class _StubEmbedder:
    async def embed_texts(self, texts, *, trace_id, model_version=None):
        class _R:
            embeddings = [[0.1, 0.2] for _ in texts]
        return _R()


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
