from uuid import uuid4

import pytest
from sqlalchemy import select

from src.infra.db.artifact_repository import ChunkRecord, VectorProjectionRecord
from src.infra.db.models import ModelReleaseModel, VectorIndexEntryModel
from src.infra.db.release_repository import ReleaseContextRepository, RollbackPreparingError
from src.infra.db.video_repository import VideoRecord


@pytest.mark.asyncio
async def test_candidate_reindexing_writes_active_and_candidate_vector_entries(
    video_repository,
    artifact_repository,
    session_factory,
) -> None:
    video_id = uuid4()
    owner_id = uuid4()
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=owner_id,
            storage_path="videos/source.mp4",
            status="UPLOADED",
        )
    )
    async with session_factory() as session:
        session.add(
            ModelReleaseModel(
                id=uuid4(),
                release_status="CANDIDATE_REINDEXING",
                active_model_version="model-v1",
                active_index_name="active-index-v1",
                candidate_model_version="model-v2",
                candidate_index_name="candidate-index-model-v2",
            )
        )
        await session.commit()

    targets = await ReleaseContextRepository(session_factory).get_online_ingest_targets(
        fallback_model_version="fallback-model"
    )
    chunks = [
        ChunkRecord(
            chunk_index=0,
            text="hello",
            enriched_text="hello caption",
            start_ms=0,
            end_ms=1,
            chunking_version="v1",
            stt_model_version="google-stt-v1",
            embedding_model_version=targets.active.model_version,
        )
    ]
    vector_projections = [
        VectorProjectionRecord(
            index_name=target.index_name,
            embedding_model_version=target.model_version,
            embeddings=[[float(index + 1), float(index + 2)]],
        )
        for index, target in enumerate(targets.all_targets)
    ]

    await artifact_repository.persist_chunks_and_vectors(
        video_id,
        chunks=chunks,
        embeddings=vector_projections[0].embeddings,
        vector_projections=vector_projections,
        set_ready=True,
    )

    async with session_factory() as session:
        vector_entries = (
            await session.execute(
                select(VectorIndexEntryModel).order_by(VectorIndexEntryModel.index_name.asc())
            )
        ).scalars().all()

    assert len(vector_entries) == 2
    assert {entry.index_name for entry in vector_entries} == {
        "active-index-v1",
        "candidate-index-model-v2",
    }
    assert {entry.chunk_id for entry in vector_entries} == {vector_entries[0].chunk_id}
    assert {entry.embedding_model_version for entry in vector_entries} == {
        "model-v1",
        "model-v2",
    }
    assert {entry.user_id for entry in vector_entries} == {owner_id}
    assert vector_entries[0].embedding_vector == pytest.approx([1.0, 2.0])
    assert vector_entries[1].embedding_vector == pytest.approx([2.0, 3.0])


@pytest.mark.asyncio
async def test_rollback_preparing_blocks_problem_model_online_ingest_targets(
    session_factory,
) -> None:
    async with session_factory() as session:
        session.add(
            ModelReleaseModel(
                id=uuid4(),
                release_status="ROLLBACK_PREPARING",
                active_model_version="problem-model-v2",
                active_index_name="problem-index-v2",
                rollback_snapshot_active_model_version="model-v1",
                rollback_snapshot_active_index_name="active-index-v1",
            )
        )
        await session.commit()

    with pytest.raises(RollbackPreparingError, match="ROLLBACK_PREPARING"):
        await ReleaseContextRepository(session_factory).get_online_ingest_targets(
            fallback_model_version="fallback-model"
        )
