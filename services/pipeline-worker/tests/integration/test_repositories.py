from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from src.infra.db.artifact_repository import AssetRecord, ChunkRecord, TranscriptSegmentRecord
from src.infra.db.models import VectorIndexEntryModel, VideoModel
from src.infra.db.video_repository import VideoRecord


@pytest.mark.asyncio
async def test_repositories_support_claim_outputs_and_delete(video_repository, artifact_repository) -> None:
    video_id = str(uuid4())
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="UPLOADED")
    )

    assert await video_repository.claim_processing(video_id) is True
    await artifact_repository.create_asset(video_id, AssetRecord(asset_type="AUDIO", storage_path="artifacts/audio.flac"))
    await artifact_repository.replace_transcripts(
        video_id,
        stt_model_version="google-stt-v1",
        segments=[TranscriptSegmentRecord(segment_index=0, text="hello", start_ms=0, end_ms=1, stt_model_version="google-stt-v1")],
    )
    await artifact_repository.persist_chunks_and_vectors(
        video_id,
        chunks=[
            ChunkRecord(
                chunk_index=0,
                text="hello",
                enriched_text="hello",
                start_ms=0,
                end_ms=1,
                chunking_version="v1",
                stt_model_version="google-stt-v1",
                embedding_model_version="v001",
            )
        ],
        embeddings=[[1.0, 2.0]],
        set_ready=True,
    )

    state = await video_repository.load_pipeline_state(
        video_id,
        stt_model_version="google-stt-v1",
        embedding_model_version="v001",
    )
    assert state.has_current_outputs is True
    assert state.video.processing_claimed_at is None
    paths = await artifact_repository.delete_video_artifacts(video_id)
    assert "artifacts/audio.flac" in paths


@pytest.mark.asyncio
async def test_persist_chunks_and_vectors_stores_vector_entries_with_video_owner(
    video_repository,
    artifact_repository,
    session_factory,
) -> None:
    video_id = str(uuid4())
    owner_id = str(uuid4())
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=owner_id, storage_path="videos/source.mp4", status="UPLOADED")
    )

    await artifact_repository.persist_chunks_and_vectors(
        video_id,
        chunks=[
            ChunkRecord(
                chunk_index=0,
                text="hello",
                enriched_text="hello caption",
                start_ms=0,
                end_ms=1,
                chunking_version="v1",
                stt_model_version="google-stt-v1",
                embedding_model_version="v001",
            )
        ],
        embeddings=[[1.0, 2.0]],
        set_ready=False,
    )

    stored_chunks = await artifact_repository.list_chunks(video_id)
    stored_vectors = await artifact_repository.list_vectors(video_id)
    stored_video = await video_repository.get_video(video_id)

    async with session_factory() as session:
        vector_entry = (
            await session.execute(select(VectorIndexEntryModel))
        ).scalar_one()

    assert stored_chunks[0].id == vector_entry.chunk_id
    assert vector_entry.user_id == stored_video.user_id
    assert vector_entry.video_id == stored_video.id
    assert vector_entry.project_id == stored_video.project_id
    assert vector_entry.index_name == "default-index"
    assert stored_vectors[0] == pytest.approx([1.0, 2.0])
    assert vector_entry.embedding_vector == pytest.approx([1.0, 2.0])


@pytest.mark.asyncio
async def test_ready_persist_rolls_back_when_delete_wins_race(
    video_repository,
    artifact_repository,
) -> None:
    video_id = str(uuid4())
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), status="UPLOADED")
    )
    assert await video_repository.claim_processing(video_id) is True
    await video_repository.set_status(video_id, "DELETING")

    persisted = await artifact_repository.persist_chunks_and_vectors(
        video_id,
        chunks=[
            ChunkRecord(
                chunk_index=0,
                text="must-not-persist",
                enriched_text="must-not-persist",
                start_ms=0,
                end_ms=1,
                chunking_version="v1",
                stt_model_version="chirp_3",
                embedding_model_version="v001",
            )
        ],
        embeddings=[[1.0, 2.0]],
        set_ready=True,
    )

    video = await video_repository.get_video(video_id)
    assert persisted is False
    assert video.status == "DELETING"
    assert await artifact_repository.list_chunks(video_id) == []


class TestProcessingClaimRecovery:
    @pytest.mark.parametrize("status", ["PENDING", "UPLOADED", "FAILED"])
    @pytest.mark.asyncio
    async def test_existing_claimable_statuses_remain_claimable(
        self,
        status: str,
        video_repository,
    ) -> None:
        video_id = str(uuid4())
        await video_repository.create_video(
            VideoRecord(
                id=video_id,
                user_id=str(uuid4()),
                storage_path="videos/source.mp4",
                status=status,
            )
        )

        assert await video_repository.claim_processing(video_id) is True

    @pytest.mark.asyncio
    async def test_fresh_processing_claim_is_rejected(
        self,
        video_repository,
        session_factory,
    ) -> None:
        video_id = str(uuid4())
        await video_repository.create_video(
            VideoRecord(
                id=video_id,
                user_id=str(uuid4()),
                storage_path="videos/source.mp4",
                status="UPLOADED",
            )
        )
        assert await video_repository.claim_processing(video_id) is True
        async with session_factory() as session:
            processing_claimed_at = await session.scalar(
                select(VideoModel.processing_claimed_at).where(
                    VideoModel.id == UUID(video_id)
                )
            )

        assert processing_claimed_at is not None
        assert await video_repository.claim_processing(video_id) is False

    @pytest.mark.asyncio
    async def test_ready_reprocessing_claim_keeps_status_and_blocks_duplicate(
        self,
        video_repository,
    ) -> None:
        video_id = str(uuid4())
        await video_repository.create_video(
            VideoRecord(id=video_id, user_id=str(uuid4()), status="READY")
        )

        assert await video_repository.claim_processing(
            video_id,
            keep_ready_status=True,
        ) is True
        claimed_video = await video_repository.get_video(video_id)

        assert claimed_video.status == "READY"
        assert claimed_video.processing_claimed_at is not None
        assert await video_repository.claim_processing(
            video_id,
            keep_ready_status=True,
        ) is False

    @pytest.mark.asyncio
    async def test_failed_processing_clears_claim(
        self,
        video_repository,
    ) -> None:
        video_id = str(uuid4())
        await video_repository.create_video(
            VideoRecord(id=video_id, user_id=str(uuid4()), status="UPLOADED")
        )
        assert await video_repository.claim_processing(video_id) is True

        marked_failed = await video_repository.set_failed(video_id, failed_stage="STT")

        failed_video = await video_repository.get_video(video_id)
        assert marked_failed is True
        assert failed_video.status == "FAILED"
        assert failed_video.processing_claimed_at is None

    @pytest.mark.asyncio
    async def test_failed_processing_does_not_overwrite_deleting_status(
        self,
        video_repository,
    ) -> None:
        video_id = str(uuid4())
        await video_repository.create_video(
            VideoRecord(id=video_id, user_id=str(uuid4()), status="UPLOADED")
        )
        assert await video_repository.claim_processing(video_id) is True
        await video_repository.set_status(video_id, "DELETING")

        marked_failed = await video_repository.set_failed(video_id, failed_stage="STT")

        deleting_video = await video_repository.get_video(video_id)
        assert marked_failed is False
        assert deleting_video.status == "DELETING"
        assert deleting_video.processing_claimed_at is None

    @pytest.mark.asyncio
    async def test_stale_processing_claim_is_reclaimed(
        self,
        video_repository,
        session_factory,
    ) -> None:
        video_id = str(uuid4())
        await video_repository.create_video(
            VideoRecord(
                id=video_id,
                user_id=str(uuid4()),
                storage_path="videos/source.mp4",
                status="UPLOADED",
            )
        )
        assert await video_repository.claim_processing(video_id) is True
        stale_claimed_at = datetime.now(timezone.utc) - timedelta(seconds=1501)
        async with session_factory() as session:
            await session.execute(
                update(VideoModel)
                .where(VideoModel.id == UUID(video_id))
                .values(processing_claimed_at=stale_claimed_at)
            )
            await session.commit()

        assert await video_repository.claim_processing(video_id) is True

    @pytest.mark.asyncio
    async def test_updated_at_change_does_not_delay_stale_reclaim(
        self,
        video_repository,
        session_factory,
    ) -> None:
        video_id = str(uuid4())
        await video_repository.create_video(
            VideoRecord(
                id=video_id,
                user_id=str(uuid4()),
                storage_path="videos/source.mp4",
                status="UPLOADED",
            )
        )
        assert await video_repository.claim_processing(video_id) is True
        stale_claimed_at = datetime.now(timezone.utc) - timedelta(seconds=1501)
        async with session_factory() as session:
            await session.execute(
                update(VideoModel)
                .where(VideoModel.id == UUID(video_id))
                .values(
                    processing_claimed_at=stale_claimed_at,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        assert await video_repository.claim_processing(video_id) is True

    @pytest.mark.asyncio
    async def test_touch_processing_refreshes_claim_and_blocks_reclaim(
        self,
        video_repository,
        session_factory,
    ) -> None:
        video_id = str(uuid4())
        await video_repository.create_video(
            VideoRecord(
                id=video_id,
                user_id=str(uuid4()),
                storage_path="videos/source.mp4",
                status="UPLOADED",
            )
        )
        assert await video_repository.claim_processing(video_id) is True
        stale_claimed_at = datetime.now(timezone.utc) - timedelta(seconds=1501)
        async with session_factory() as session:
            await session.execute(
                update(VideoModel)
                .where(VideoModel.id == UUID(video_id))
                .values(processing_claimed_at=stale_claimed_at)
            )
            await session.commit()

        await video_repository.touch_processing(video_id)

        assert await video_repository.claim_processing(video_id) is False

    @pytest.mark.asyncio
    async def test_deleting_video_is_not_reclaimed(self, video_repository) -> None:
        video_id = str(uuid4())
        await video_repository.create_video(
            VideoRecord(
                id=video_id,
                user_id=str(uuid4()),
                storage_path="videos/source.mp4",
                status="DELETING",
            )
        )

        assert await video_repository.claim_processing(video_id) is False
