import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.dataset.batch import DatasetBatchService
from src.dataset.manifest import DatasetEligibilityPolicy
from src.dataset.materializer import DatasetMaterializer
from src.infra.db.models import Base, ChunkModel, ProjectModel, VideoModel
from src.infra.db.stores import DbChunkTextSnapshot
from src.infra.storage.inmemory import InMemoryArtifactStore


class TestDbChunkTextSnapshot:
    @pytest.fixture
    async def session(self) -> AsyncSession:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        try:
            async with factory() as db_session:
                yield db_session
        finally:
            await engine.dispose()

    async def test_text_by_chunk_id_returns_requested_chunk_texts(self, session: AsyncSession) -> None:
        _, _, chunk = await _seed_project_video_chunk(
            session,
            chunk_text="semantic search ranking",
        )

        snapshot = DbChunkTextSnapshot(session)

        result = await snapshot.text_by_chunk_id({str(chunk.id), str(uuid4())})

        assert result == {str(chunk.id): "semantic search ranking"}

    async def test_random_negative_pool_filters_ready_project_scope_and_excludes_chunks(
        self,
        session: AsyncSession,
    ) -> None:
        project, _, excluded_chunk = await _seed_project_video_chunk(
            session,
            chunk_text="used chunk",
        )
        _, _, candidate = await _seed_project_video_chunk(
            session,
            project_id=project.id,
            user_id=project.user_id,
            chunk_text="random candidate",
        )
        await _seed_project_video_chunk(
            session,
            project_id=project.id,
            user_id=project.user_id,
            video_status="PROCESSING",
            chunk_text="not ready candidate",
        )
        excluded_project, _, other_chunk = await _seed_project_video_chunk(
            session,
            chunk_text="other project candidate",
        )
        await session.flush()

        snapshot = DbChunkTextSnapshot(session)

        result = await snapshot.random_negative_pool(
            {str(project.id), str(excluded_project.id)},
            {
                str(project.id): {str(excluded_chunk.id)},
                str(excluded_project.id): {str(other_chunk.id)},
            },
            10,
        )

        assert result == {str(project.id): {str(candidate.id): "random candidate"}}

    async def test_random_negative_pool_applies_per_project_limit(self, session: AsyncSession) -> None:
        project, _, _ = await _seed_project_video_chunk(session, chunk_text="candidate 0")
        for index in range(1, 5):
            await _seed_project_video_chunk(
                session,
                project_id=project.id,
                user_id=project.user_id,
                chunk_text=f"candidate {index}",
            )
        await session.flush()

        snapshot = DbChunkTextSnapshot(session)

        result = await snapshot.random_negative_pool({str(project.id)}, {}, 3)

        assert len(result[str(project.id)]) == 3

    async def test_dataset_batch_service_uses_db_random_negative_pool(
        self,
        session: AsyncSession,
        tmp_path,
    ) -> None:
        project, _, positive = await _seed_project_video_chunk(
            session,
            chunk_text="semantic search ranking",
        )
        _, _, exposed_unused = await _seed_project_video_chunk(
            session,
            project_id=project.id,
            user_id=project.user_id,
            chunk_text="unrelated cooking recipe",
        )
        _, _, random_candidate = await _seed_project_video_chunk(
            session,
            project_id=project.id,
            user_id=project.user_id,
            chunk_text="same project archive note",
        )
        await session.flush()
        store = InMemoryArtifactStore(
            {
                "feedback/raw/events.jsonl": (
                    json.dumps(
                        {
                            "event_id": "event-1",
                            "trace_id": "trace-1",
                            "req_id": "req-1",
                            "user_id": str(project.user_id),
                            "project_id": str(project.id),
                            "query_text": "semantic search",
                            "rating": "LIKE",
                            "topk_ids": [str(positive.id), str(exposed_unused.id)],
                            "used_ids": [str(positive.id)],
                            "active_model_version": "baseline-v1",
                            "active_index_name": "active-index-v1",
                            "response_snapshot_ref": "snapshot:req-1",
                            "created_at": "2026-05-17T11:55:00Z",
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode(),
            }
        )

        refs = await DatasetBatchService(
            artifact_store=store,
            chunk_text_snapshot=DbChunkTextSnapshot(session),
            materializer=DatasetMaterializer(
                eligibility_policy=DatasetEligibilityPolicy(
                    min_training_group_count=1,
                    min_negative_count=1,
                )
            ),
            clock=_FixedClock(),
        ).materialize_latest(
            raw_feedback_log_prefix="feedback/raw/",
            dataset_artifact_prefix="feedback/datasets",
            workspace_dir=tmp_path,
            source_window_start=datetime(2026, 5, 17, 0, 0, tzinfo=UTC),
            source_window_end=datetime(2026, 5, 18, 0, 0, tzinfo=UTC),
        )
        rows = [
            json.loads(line)
            for line in store.objects[refs.rows_storage_path].decode().splitlines()
        ]
        exposed_unused_negative = rows[0]["negatives"][0]
        random_negative = rows[0]["negatives"][1]

        assert exposed_unused_negative["chunk_id"] == str(exposed_unused.id)
        assert exposed_unused_negative["confidence"] == pytest.approx(0.4)
        assert exposed_unused_negative["source"] == "exposed_unused"
        assert exposed_unused_negative["text"] == "unrelated cooking recipe"
        assert random_negative["chunk_id"] == str(random_candidate.id)
        assert random_negative["confidence"] == pytest.approx(0.2)
        assert random_negative["source"] == "random_same_project"
        assert random_negative["text"] == "same project archive note"


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


async def _seed_project_video_chunk(
    session: AsyncSession,
    *,
    project_id=None,
    user_id=None,
    video_status: str = "READY",
    chunk_text: str,
) -> tuple[ProjectModel, VideoModel, ChunkModel]:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    owner_id = user_id or uuid4()
    if project_id is None:
        project = ProjectModel(
            id=uuid4(),
            user_id=owner_id,
            title="Project",
            created_at=now,
            updated_at=now,
        )
        session.add(project)
    else:
        project = await session.get(ProjectModel, project_id)
        if project is None:
            raise ValueError("project_id must reference an existing project")
    video = VideoModel(
        user_id=owner_id,
        project_id=project.id,
        title="Video",
        status=video_status,
        updated_at=now,
    )
    session.add(video)
    await session.flush()
    chunk = ChunkModel(
        video_id=video.id,
        text=chunk_text,
        embedding_model_version="baseline-v1",
    )
    session.add(chunk)
    await session.flush()
    return project, video, chunk
