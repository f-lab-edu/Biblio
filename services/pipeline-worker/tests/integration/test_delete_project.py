from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from src.infra.db.artifact_repository import AssetRecord, ChunkRecord
from src.infra.db.models import (
    AssetModel,
    ChunkModel,
    LegacyReindexItemModel,
    ProjectModel,
    SearchConversationModel,
    SearchResponseSnapshotModel,
    TranscriptSegmentModel,
    VectorIndexEntryModel,
    VideoModel,
)
from src.infra.db.video_repository import VideoRecord
from src.usecases.delete_project import DeleteProjectUseCase
from src.usecases.delete_video import DeletionDeferred


@pytest.mark.asyncio
async def test_delete_project_cascades_videos_search_records_and_storage(
    session_factory,
    video_repository,
    artifact_repository,
    delete_video_use_case,
    storage_client,
) -> None:
    user_id = uuid4()
    project_id = uuid4()
    first_video_id = uuid4()
    second_video_id = uuid4()
    storage_client.objects["videos/first.mp4"] = b"first"
    storage_client.objects["videos/second.mp4"] = b"second"
    storage_client.objects["artifacts/first.flac"] = b"audio"

    async with session_factory() as session:
        session.add(ProjectModel(id=project_id, user_id=user_id, title="Project"))
        session.add(
            SearchResponseSnapshotModel(
                req_id=uuid4(),
                user_id=user_id,
                project_id=project_id,
                query_text="query",
                topk_chunk_ids=[],
                used_chunk_ids=[],
                active_model_version="model-v1",
                active_index_name="index-v1",
                served_vector_paths=[],
                project_serving_state="SERVABLE",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        session.add(
            SearchConversationModel(
                req_id=uuid4(),
                user_id=user_id,
                project_id=project_id,
                query="query",
                answer="answer",
                sources=[],
            )
        )
        await session.commit()

    await video_repository.create_video(
        VideoRecord(
            id=first_video_id,
            user_id=user_id,
            project_id=project_id,
            storage_path="videos/first.mp4",
            status="DELETING",
        )
    )
    await video_repository.create_video(
        VideoRecord(
            id=second_video_id,
            user_id=user_id,
            project_id=project_id,
            storage_path="videos/second.mp4",
            status="DELETING",
        )
    )
    async with session_factory() as session:
        session.add(
            LegacyReindexItemModel(
                id=uuid4(),
                video_id=first_video_id,
                user_id=user_id,
                project_id=project_id,
                source_index_name="old-index",
                source_model_version="old-model",
                target_index_name="new-index",
                target_model_version="new-model",
                status="PENDING",
            )
        )
        await session.commit()
    await artifact_repository.create_asset(
        first_video_id,
        AssetRecord(asset_type="AUDIO", storage_path="artifacts/first.flac"),
    )
    await artifact_repository.persist_chunks_and_vectors(
        first_video_id,
        chunks=[
            ChunkRecord(
                chunk_index=0,
                text="hello",
                enriched_text="hello",
                start_ms=0,
                end_ms=1000,
                chunking_version="v1",
                stt_model_version="chirp_2",
                embedding_model_version="v001",
            )
        ],
        embeddings=[[0.1, 0.2]],
        set_ready=False,
    )

    use_case = DeleteProjectUseCase(
        video_repository=video_repository,
        delete_video_use_case=delete_video_use_case,
        session_factory=session_factory,
    )

    await use_case.execute(project_id=str(project_id), trace_id="trace-project-delete")

    async with session_factory() as session:
        counts = {
            "project": await session.scalar(select(func.count()).select_from(ProjectModel)),
            "snapshot": await session.scalar(select(func.count()).select_from(SearchResponseSnapshotModel)),
            "conversation": await session.scalar(select(func.count()).select_from(SearchConversationModel)),
            "asset": await session.scalar(select(func.count()).select_from(AssetModel)),
            "transcript": await session.scalar(select(func.count()).select_from(TranscriptSegmentModel)),
            "chunk": await session.scalar(select(func.count()).select_from(ChunkModel)),
            "vector": await session.scalar(select(func.count()).select_from(VectorIndexEntryModel)),
            "legacy": await session.scalar(select(func.count()).select_from(LegacyReindexItemModel)),
        }

    assert counts == {
        "project": 0,
        "snapshot": 0,
        "conversation": 0,
        "asset": 0,
        "transcript": 0,
        "chunk": 0,
        "vector": 0,
        "legacy": 0,
    }
    assert await video_repository.get_video(first_video_id) is None
    assert await video_repository.get_video(second_video_id) is None
    assert set(storage_client.deleted_paths) == {
        "videos/first.mp4",
        "videos/second.mp4",
        "artifacts/first.flac",
    }


@pytest.mark.asyncio
async def test_delete_project_defers_fresh_processing_claim_but_allows_stale_claim(
    session_factory,
    video_repository,
    delete_video_use_case,
    storage_client,
) -> None:
    user_id = uuid4()
    project_id = uuid4()
    video_id = uuid4()
    storage_client.objects["videos/processing.mp4"] = b"video"
    async with session_factory() as session:
        session.add(ProjectModel(id=project_id, user_id=user_id, title="Project"))
        await session.commit()
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=user_id,
            project_id=project_id,
            storage_path="videos/processing.mp4",
            status="UPLOADED",
        )
    )
    assert await video_repository.claim_processing(video_id) is True
    use_case = DeleteProjectUseCase(
        video_repository=video_repository,
        delete_video_use_case=delete_video_use_case,
        session_factory=session_factory,
    )

    with pytest.raises(DeletionDeferred):
        await use_case.execute(project_id=str(project_id), trace_id="trace-project-defer")

    assert await video_repository.get_video(video_id) is not None
    async with session_factory() as session:
        await session.execute(
            update(VideoModel)
            .where(VideoModel.id == video_id)
            .values(processing_claimed_at=datetime.now(UTC) - timedelta(seconds=1501))
        )
        await session.commit()

    result = await use_case.execute(
        project_id=str(project_id),
        trace_id="trace-project-stale-delete",
    )

    assert result.deleted_video_count == 1
    assert await video_repository.get_video(video_id) is None
