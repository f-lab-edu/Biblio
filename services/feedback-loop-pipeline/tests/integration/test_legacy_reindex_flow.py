from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.infra.db.legacy_reindex_store import LegacyReindexStore, VectorIndexCatalogStore
from src.infra.db.models import (
    Base,
    ChunkModel,
    MLPipelineRunModel,
    LegacyReindexItemModel,
    ModelReleaseModel,
    ProjectModel,
    VectorIndexEntryModel,
    VectorIndexCatalogModel,
    VideoModel,
)
from src.infra.db.stores import MLPipelineRunStore, ModelReleaseStore, VectorIndexProjectionReader
from src.release.transition import ServingTransitionManager
from src.release.legacy_reindex import LegacyReindexCoordinator


@dataclass(frozen=True)
class _EmbeddingBatch:
    embeddings: list[list[float]]
    model_version: str


class _FakeEmbeddingClient:
    def __init__(self, *, dimensions: int = 3) -> None:
        self.dimensions = dimensions
        self.calls: list[dict[str, object]] = []

    async def embed_texts(
        self,
        texts: list[str],
        *,
        trace_id: str,
        model_version: str | None = None,
    ) -> _EmbeddingBatch:
        # Async to satisfy the embedding client port used by the coordinator.
        self.calls.append(
            {
                "texts": texts,
                "trace_id": trace_id,
                "model_version": model_version,
            }
        )
        return _EmbeddingBatch(
            embeddings=[
                [float(len(text)), float(index), float(self.dimensions)]
                for index, text in enumerate(texts)
            ],
            model_version=model_version or "model-b",
        )


class _FailingEmbeddingClient:
    async def embed_texts(
        self,
        texts: list[str],
        *,
        trace_id: str,
        model_version: str | None = None,
    ) -> _EmbeddingBatch:
        del texts, trace_id, model_version
        raise RuntimeError("embedding endpoint unavailable")


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as db_session:
            yield db_session
    finally:
        await engine.dispose()


async def test_reindexes_old_only_video_into_active_index(session: AsyncSession) -> None:
    now = datetime(2026, 5, 27, 9, 0, tzinfo=UTC)
    user_id = uuid4()
    project, video = await _seed_project_video(session, user_id=user_id, now=now)
    chunk = await _seed_chunk(
        session,
        video_id=video.id,
        text="plain transcript",
        enriched_text="visual enriched transcript",
    )
    source_created_at = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)
    await _seed_release_and_catalog(
        session,
        active_index="index-b",
        active_model="model-b",
        previous_index="index-a",
        previous_model="model-a",
        now=now,
    )
    session.add(
        VectorIndexEntryModel(
            index_name="index-a",
            chunk_id=chunk.id,
            user_id=user_id,
            project_id=project.id,
            video_id=video.id,
            embedding_model_version="model-a",
            embedding_vector=[1.0, 0.0, 3.0],
            created_at=source_created_at,
        )
    )
    await session.flush()

    embedding_client = _FakeEmbeddingClient()
    result = await _coordinator(session, embedding_client=embedding_client).run_once(
        trace_id=uuid4()
    )

    assert result.enqueued_item_count == 1
    assert result.succeeded_item_count == 1
    assert embedding_client.calls[0]["texts"] == ["visual enriched transcript"]
    assert embedding_client.calls[0]["model_version"] == "model-b"

    source_entry = await session.get(VectorIndexEntryModel, ("index-a", chunk.id))
    target_entry = await session.get(VectorIndexEntryModel, ("index-b", chunk.id))
    assert source_entry is not None
    assert target_entry is not None
    assert target_entry.embedding_model_version == "model-b"
    assert target_entry.embedding_vector == pytest.approx([26.0, 0.0, 3.0])
    assert target_entry.created_at.replace(tzinfo=UTC) == source_created_at

    item = await session.scalar(select(LegacyReindexItemModel))
    assert item is not None
    assert item.status == "SUCCEEDED"
    assert item.total_chunk_count == 1
    assert item.completed_chunk_count == 1


async def test_reindex_uses_text_fallback_when_enriched_text_is_blank(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 5, 27, 9, 0, tzinfo=UTC)
    user_id = uuid4()
    project, video = await _seed_project_video(session, user_id=user_id, now=now)
    chunk = await _seed_chunk(session, video_id=video.id, text="plain transcript", enriched_text="")
    await _seed_release_and_catalog(
        session,
        active_index="index-b",
        active_model="model-b",
        previous_index="index-a",
        previous_model="model-a",
        now=now,
    )
    session.add(
        VectorIndexEntryModel(
            index_name="index-a",
            chunk_id=chunk.id,
            user_id=user_id,
            project_id=project.id,
            video_id=video.id,
            embedding_model_version="model-a",
            embedding_vector=[1.0, 0.0, 3.0],
            created_at=now,
        )
    )
    await session.flush()

    embedding_client = _FakeEmbeddingClient()
    result = await _coordinator(session, embedding_client=embedding_client).run_once(
        trace_id=uuid4()
    )

    assert result.succeeded_item_count == 1
    assert embedding_client.calls[0]["texts"] == ["plain transcript"]


async def test_deleting_video_item_is_skipped_without_vector_upsert(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 5, 27, 9, 0, tzinfo=UTC)
    user_id = uuid4()
    project, video = await _seed_project_video(session, user_id=user_id, now=now)
    chunk = await _seed_chunk(session, video_id=video.id, text="plain transcript")
    await _seed_release_and_catalog(
        session,
        active_index="index-b",
        active_model="model-b",
        previous_index="index-a",
        previous_model="model-a",
        now=now,
    )
    session.add_all(
        [
            VectorIndexEntryModel(
                index_name="index-a",
                chunk_id=chunk.id,
                user_id=user_id,
                project_id=project.id,
                video_id=video.id,
                embedding_model_version="model-a",
                embedding_vector=[1.0, 0.0, 3.0],
                created_at=now,
            ),
            LegacyReindexItemModel(
                video_id=video.id,
                user_id=user_id,
                project_id=project.id,
                source_index_name="index-a",
                source_model_version="model-a",
                target_index_name="index-b",
                target_model_version="model-b",
                status="PENDING",
                retry_count=0,
                total_chunk_count=0,
                completed_chunk_count=0,
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    await session.flush()
    video.status = "DELETING"
    await session.flush()

    embedding_client = _FakeEmbeddingClient()
    result = await _coordinator(session, embedding_client=embedding_client).run_once(
        trace_id=uuid4()
    )

    assert result.skipped_item_count == 1
    assert embedding_client.calls == []
    assert await session.get(VectorIndexEntryModel, ("index-b", chunk.id)) is None

    item = await session.scalar(select(LegacyReindexItemModel))
    assert item is not None
    assert item.status == "SKIPPED"
    assert item.last_error == "video is deleting"


async def test_vector_dimension_mismatch_marks_item_failed(session: AsyncSession) -> None:
    now = datetime(2026, 5, 27, 9, 0, tzinfo=UTC)
    user_id = uuid4()
    project, video = await _seed_project_video(session, user_id=user_id, now=now)
    chunk = await _seed_chunk(session, video_id=video.id, text="plain transcript")
    await _seed_release_and_catalog(
        session,
        active_index="index-b",
        active_model="model-b",
        previous_index="index-a",
        previous_model="model-a",
        now=now,
        embedding_dimension=4,
    )
    session.add(
        VectorIndexEntryModel(
            index_name="index-a",
            chunk_id=chunk.id,
            user_id=user_id,
            project_id=project.id,
            video_id=video.id,
            embedding_model_version="model-a",
            embedding_vector=[1.0, 0.0, 3.0],
            created_at=now,
        )
    )
    await session.flush()

    result = await _coordinator(
        session,
        embedding_client=_FakeEmbeddingClient(dimensions=3),
    ).run_once(trace_id=uuid4())

    assert result.failed_item_count == 1
    item = await session.scalar(select(LegacyReindexItemModel))
    assert item is not None
    assert item.status == "FAILED"
    assert item.failed_stage == "VECTOR_UPSERT"
    assert item.failure_type == "ERROR"
    assert item.last_error == "embedding dimension mismatch"


async def test_embedding_failure_marks_item_failed(session: AsyncSession) -> None:
    now = datetime(2026, 5, 27, 9, 0, tzinfo=UTC)
    user_id = uuid4()
    project, video = await _seed_project_video(session, user_id=user_id, now=now)
    chunk = await _seed_chunk(session, video_id=video.id, text="plain transcript")
    await _seed_release_and_catalog(
        session,
        active_index="index-b",
        active_model="model-b",
        previous_index="index-a",
        previous_model="model-a",
        now=now,
    )
    session.add(
        VectorIndexEntryModel(
            index_name="index-a",
            chunk_id=chunk.id,
            user_id=user_id,
            project_id=project.id,
            video_id=video.id,
            embedding_model_version="model-a",
            embedding_vector=[1.0, 0.0, 3.0],
            created_at=now,
        )
    )
    await session.flush()

    result = await _coordinator(
        session,
        embedding_client=_FailingEmbeddingClient(),
    ).run_once(trace_id=uuid4())

    assert result.failed_item_count == 1
    item = await session.scalar(select(LegacyReindexItemModel))
    assert item is not None
    assert item.status == "FAILED"
    assert item.failed_stage == "EMBEDDING"
    assert item.last_error == "embedding endpoint unavailable"
    assert await session.get(VectorIndexEntryModel, ("index-b", chunk.id)) is None


async def test_cutover_blocks_and_enqueues_when_legacy_vectors_remain(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 5, 27, 9, 0, tzinfo=UTC)
    user_id = uuid4()
    project, video = await _seed_project_video(session, user_id=user_id, now=now)
    chunk = await _seed_chunk(session, video_id=video.id, text="plain transcript")
    run = MLPipelineRunModel(
        status="READY_FOR_RELEASE",
        dataset_version="dataset-v1",
        baseline_model_version="model-b",
        candidate_model_version="model-c",
        candidate_index_name="index-c",
        created_at=now,
        updated_at=now,
    )
    release = ModelReleaseModel(
        release_status="CANDIDATE_REINDEXING",
        active_model_version="model-b",
        active_index_name="index-b",
        previous_model_version="model-a",
        previous_index_name="index-a",
        candidate_model_version="model-c",
        candidate_index_name="index-c",
        candidate_opened_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add_all(
        [
            run,
            release,
            VectorIndexCatalogModel(
                index_name="index-a",
                model_version="model-a",
                embedding_dimension=3,
                created_at=now,
            ),
            VectorIndexCatalogModel(
                index_name="index-b",
                model_version="model-b",
                embedding_dimension=3,
                created_at=now,
            ),
            VectorIndexCatalogModel(
                index_name="index-c",
                model_version="model-c",
                embedding_dimension=3,
                created_at=now,
            ),
            VectorIndexEntryModel(
                index_name="index-a",
                chunk_id=chunk.id,
                user_id=user_id,
                project_id=project.id,
                video_id=video.id,
                embedding_model_version="model-a",
                embedding_vector=[1.0, 0.0, 3.0],
                created_at=now,
            ),
        ]
    )
    await session.flush()

    result = await ServingTransitionManager(
        run_store=MLPipelineRunStore(session),
        release_store=ModelReleaseStore(session),
        vector_reader=VectorIndexProjectionReader(session),
        legacy_reindex_gate=LegacyReindexStore(session),
    ).cutover_candidate_release(run_id=run.id, trace_id=uuid4())

    assert result.status == "blocked_legacy_vectors_remain"
    assert result.legacy_reindex_video_count == 1
    assert release.release_status == "CANDIDATE_REINDEXING"

    item = await session.scalar(select(LegacyReindexItemModel))
    assert item is not None
    assert item.video_id == video.id
    assert item.source_index_name == "index-a"
    assert item.target_index_name == "index-b"
    assert item.status == "PENDING"


async def _seed_project_video(
    session: AsyncSession,
    *,
    user_id: UUID,
    now: datetime,
) -> tuple[ProjectModel, VideoModel]:
    project = ProjectModel(user_id=user_id, title="Project", created_at=now, updated_at=now)
    session.add(project)
    await session.flush()
    video = VideoModel(
        user_id=user_id,
        project_id=project.id,
        title="Video",
        status="READY",
        updated_at=now,
    )
    session.add(video)
    await session.flush()
    return project, video


async def _seed_chunk(
    session: AsyncSession,
    *,
    video_id: UUID,
    text: str,
    enriched_text: str | None = None,
) -> ChunkModel:
    chunk = ChunkModel(
        video_id=video_id,
        chunk_index=0,
        text=text,
        enriched_text=enriched_text,
        embedding_model_version="model-a",
    )
    session.add(chunk)
    await session.flush()
    return chunk


async def _seed_release_and_catalog(
    session: AsyncSession,
    *,
    active_index: str,
    active_model: str,
    previous_index: str,
    previous_model: str,
    now: datetime,
    embedding_dimension: int = 3,
) -> None:
    session.add(
        ModelReleaseModel(
            release_status="STABLE",
            active_model_version=active_model,
            active_index_name=active_index,
            previous_model_version=previous_model,
            previous_index_name=previous_index,
            created_at=now,
            updated_at=now,
        )
    )
    session.add_all(
        [
            VectorIndexCatalogModel(
                index_name=previous_index,
                model_version=previous_model,
                embedding_dimension=embedding_dimension,
                created_at=now,
            ),
            VectorIndexCatalogModel(
                index_name=active_index,
                model_version=active_model,
                embedding_dimension=embedding_dimension,
                created_at=now,
            ),
        ]
    )
    await session.flush()


def _coordinator(
    session: AsyncSession,
    *,
    embedding_client: object,
) -> LegacyReindexCoordinator:
    return LegacyReindexCoordinator(
        legacy_store=LegacyReindexStore(session),
        catalog_store=VectorIndexCatalogStore(session),
        embedding_client=embedding_client,
        batch_size=8,
        per_run_video_limit=100,
        throttle_sleep_ms=0,
        release_store=ModelReleaseStore(session),
    )
