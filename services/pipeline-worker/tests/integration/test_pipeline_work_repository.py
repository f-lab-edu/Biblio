from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.infra.db.models import (
    PipelineAudioPartModel,
    PipelineChunkWorkModel,
    PipelineEmbeddingBatchModel,
    PipelineRunModel,
    VideoModel,
)
from src.infra.db.pipeline_work_repository import (
    PipelineVideoDeletingError,
    PipelineVideoNotFoundError,
    PipelineVideoNotDeletingError,
    PipelineWorkRepository,
)


async def _create_video(session_factory, *, status: str = "PROCESSING"):
    video_id = uuid4()
    async with session_factory() as session:
        async with session.begin():
            session.add(
                VideoModel(
                    id=video_id,
                    user_id=uuid4(),
                    title="pipeline work test",
                    category="test",
                    input_type="FILE",
                    status=status,
                )
            )
    return video_id


def _make_audio_part(
    run_id,
    *,
    part_index: int = 0,
    status: str,
) -> PipelineAudioPartModel:
    return PipelineAudioPartModel(
        audio_part_id=uuid4(),
        pipeline_run_id=run_id,
        part_index=part_index,
        start_ms=part_index * 1_000,
        end_ms=(part_index + 1) * 1_000,
        audio_gcs_path="audio/test.flac",
        stt_model_version="chirp_2",
        status=status,
    )


def _make_chunk_work(
    run_id,
    *,
    chunk_index: int,
    enrichment_status: str,
    embedding_status: str,
    embedding_batch_id=None,
) -> PipelineChunkWorkModel:
    return PipelineChunkWorkModel(
        chunk_work_id=uuid4(),
        pipeline_run_id=run_id,
        chunk_index=chunk_index,
        text=f"chunk {chunk_index}",
        start_ms=chunk_index * 1_000,
        end_ms=(chunk_index + 1) * 1_000,
        chunking_version="v1",
        stt_model_version="chirp_2",
        embedding_model_version="v001",
        index_name="video-chunks",
        enrichment_status=enrichment_status,
        embedding_status=embedding_status,
        embedding_batch_id=embedding_batch_id,
    )


class TestCreatePipelineRun:
    @pytest.mark.asyncio
    async def test_creates_first_active_run(self, session_factory):
        video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)

        created = await repository.create_pipeline_run(video_id, "pipeline-v1")

        assert created.video_id == video_id
        assert created.pipeline_version == "pipeline-v1"
        assert created.status == "RUNNING"
        assert created.is_active is True
        assert created.normalization_status == "READY"
        assert created.normalization_timestamps.ready_at is not None
        assert created.created_at is not None

    @pytest.mark.asyncio
    async def test_supersedes_active_run_before_creating_new_run(self, session_factory):
        video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)
        previous = await repository.create_pipeline_run(video_id, "pipeline-v1")

        current = await repository.create_pipeline_run(video_id, "pipeline-v2")

        async with session_factory() as session:
            models = list(
                await session.scalars(
                    select(PipelineRunModel).where(
                        PipelineRunModel.video_id == video_id
                    )
                )
            )
        by_id = {model.id: model for model in models}
        assert by_id[previous.id].status == "SUPERSEDED"
        assert by_id[previous.id].is_active is False
        assert by_id[current.id].status == "RUNNING"
        assert by_id[current.id].is_active is True

    @pytest.mark.asyncio
    async def test_rejects_missing_video_without_creating_run(self, session_factory):
        repository = PipelineWorkRepository(session_factory)

        with pytest.raises(PipelineVideoNotFoundError):
            await repository.create_pipeline_run(uuid4(), "pipeline-v1")

        async with session_factory() as session:
            assert await session.scalar(select(PipelineRunModel.id)) is None

    @pytest.mark.asyncio
    async def test_rejects_deleting_video_without_creating_run(self, session_factory):
        video_id = await _create_video(session_factory, status="DELETING")
        repository = PipelineWorkRepository(session_factory)

        with pytest.raises(PipelineVideoDeletingError):
            await repository.create_pipeline_run(video_id, "pipeline-v1")

        async with session_factory() as session:
            assert await session.scalar(select(PipelineRunModel.id)) is None


class TestGetActivePipelineRun:
    @pytest.mark.asyncio
    async def test_returns_current_run_after_replacement(self, session_factory):
        video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)
        await repository.create_pipeline_run(video_id, "pipeline-v1")
        current = await repository.create_pipeline_run(video_id, "pipeline-v2")

        found = await repository.get_active_pipeline_run(video_id)

        assert found is not None
        assert found.id == current.id

    @pytest.mark.asyncio
    async def test_returns_none_when_video_has_no_run(self, session_factory):
        video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)

        assert await repository.get_active_pipeline_run(video_id) is None


class TestStartPipelineRun:
    @pytest.mark.asyncio
    async def test_reuses_active_run_when_preprocess_is_redelivered(
        self,
        session_factory,
    ):
        video_id = await _create_video(session_factory, status="UPLOADED")
        repository = PipelineWorkRepository(session_factory)

        first = await repository.start_pipeline_run(video_id, "work-unit-v1")
        duplicate = await repository.start_pipeline_run(video_id, "work-unit-v1")

        assert first is not None
        assert duplicate is not None
        assert duplicate.id == first.id
        async with session_factory() as session:
            video = await session.get(VideoModel, video_id)
            assert video.status == "PROCESSING"
            assert len(list(await session.scalars(select(PipelineRunModel)))) == 1

    @pytest.mark.asyncio
    async def test_does_not_restart_completed_or_failed_video(self, session_factory):
        repository = PipelineWorkRepository(session_factory)
        ready_video_id = await _create_video(session_factory, status="READY")
        failed_video_id = await _create_video(session_factory, status="FAILED")

        assert await repository.start_pipeline_run(ready_video_id, "work-unit-v1") is None
        assert await repository.start_pipeline_run(failed_video_id, "work-unit-v1") is None


class TestDeletionWorkState:
    @pytest.mark.asyncio
    async def test_counts_running_work_by_execution_unit(self, session_factory):
        video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)
        run = await repository.create_pipeline_run(video_id, "pipeline-v1")
        batch_id = uuid4()

        async with session_factory() as session:
            async with session.begin():
                model = await session.get(PipelineRunModel, run.id)
                model.normalization_status = "RUNNING"
                session.add(
                    PipelineEmbeddingBatchModel(
                        batch_id=batch_id,
                        chunk_work_ids=[],
                        embedding_model_version="v001",
                        index_name="video-chunks",
                        status="RUNNING",
                    )
                )
                session.add(_make_audio_part(run.id, status="RUNNING"))
                session.add_all(
                    [
                        _make_chunk_work(
                            run.id,
                            chunk_index=0,
                            enrichment_status="RUNNING",
                            embedding_status="RUNNING",
                            embedding_batch_id=batch_id,
                        ),
                        _make_chunk_work(
                            run.id,
                            chunk_index=1,
                            enrichment_status="READY",
                            embedding_status="RUNNING",
                            embedding_batch_id=batch_id,
                        ),
                    ]
                )

        assert await repository.count_running_work(video_id) == 4
        assert await repository.is_deletion_waiting(video_id) is True

    @pytest.mark.asyncio
    async def test_reports_no_deletion_wait_for_ready_work(self, session_factory):
        video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)
        await repository.create_pipeline_run(video_id, "pipeline-v1")

        assert await repository.count_running_work(video_id) == 0
        assert await repository.is_deletion_waiting(video_id) is False

    @pytest.mark.asyncio
    async def test_cancels_only_ready_and_dispatched_work(self, session_factory):
        video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)
        run = await repository.create_pipeline_run(video_id, "pipeline-v1")

        async with session_factory() as session:
            async with session.begin():
                video = await session.get(VideoModel, video_id)
                video.status = "DELETING"
                session.add_all(
                    [
                        _make_audio_part(run.id, part_index=0, status="READY"),
                        _make_audio_part(
                            run.id,
                            part_index=1,
                            status="DISPATCHED",
                        ),
                        _make_audio_part(run.id, part_index=2, status="RUNNING"),
                        _make_audio_part(
                            run.id,
                            part_index=3,
                            status="COMPLETED",
                        ),
                        _make_chunk_work(
                            run.id,
                            chunk_index=0,
                            enrichment_status="READY",
                            embedding_status="READY",
                        ),
                        _make_chunk_work(
                            run.id,
                            chunk_index=1,
                            enrichment_status="DISPATCHED",
                            embedding_status="DISPATCHED",
                        ),
                        _make_chunk_work(
                            run.id,
                            chunk_index=2,
                            enrichment_status="RUNNING",
                            embedding_status="RUNNING",
                        ),
                        _make_chunk_work(
                            run.id,
                            chunk_index=3,
                            enrichment_status="COMPLETED",
                            embedding_status="COMPLETED",
                        ),
                    ]
                )

        cancelled = await repository.cancel_pending_work_for_deleting_video(video_id)

        async with session_factory() as session:
            run_model = await session.get(PipelineRunModel, run.id)
            audio_statuses = set(
                await session.scalars(select(PipelineAudioPartModel.status))
            )
            chunk_models = list(
                await session.scalars(
                    select(PipelineChunkWorkModel).order_by(
                        PipelineChunkWorkModel.chunk_index
                    )
                )
            )

        assert cancelled == 7
        assert run_model.status == "RUNNING"
        assert run_model.is_active is True
        assert run_model.normalization_status == "CANCELLED"
        assert audio_statuses == {"CANCELLED", "RUNNING", "COMPLETED"}
        assert [
            (model.enrichment_status, model.embedding_status)
            for model in chunk_models
        ] == [
            ("CANCELLED", "CANCELLED"),
            ("CANCELLED", "CANCELLED"),
            ("RUNNING", "RUNNING"),
            ("COMPLETED", "COMPLETED"),
        ]

    @pytest.mark.asyncio
    async def test_rejects_cancellation_when_video_is_not_deleting(
        self,
        session_factory,
    ):
        video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)
        await repository.create_pipeline_run(video_id, "pipeline-v1")

        with pytest.raises(PipelineVideoNotDeletingError):
            await repository.cancel_pending_work_for_deleting_video(video_id)


class TestDispatchedRecovery:
    @pytest.mark.asyncio
    async def test_recovers_only_expired_dispatched_normalization(self, session_factory):
        first_video_id = await _create_video(session_factory)
        second_video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)
        expired = await repository.create_pipeline_run(first_video_id, "work-unit-v1")
        fresh = await repository.create_pipeline_run(second_video_id, "work-unit-v1")
        now = datetime.now(UTC)

        async with session_factory() as session:
            async with session.begin():
                expired_model = await session.get(PipelineRunModel, expired.id)
                fresh_model = await session.get(PipelineRunModel, fresh.id)
                expired_model.normalization_status = "DISPATCHED"
                expired_model.normalization_message_id = 10
                expired_model.normalization_dispatched_at = now - timedelta(seconds=61)
                fresh_model.normalization_status = "DISPATCHED"
                fresh_model.normalization_message_id = 11
                fresh_model.normalization_dispatched_at = now

        recovered = await repository.recover_stale_dispatched_work(
            "NORMALIZE_VIDEO",
            visibility_timeout_sec=60,
        )

        async with session_factory() as session:
            expired_model = await session.get(PipelineRunModel, expired.id)
            fresh_model = await session.get(PipelineRunModel, fresh.id)
        assert recovered == 1
        assert expired_model.normalization_status == "READY"
        assert expired_model.normalization_message_id is None
        assert fresh_model.normalization_status == "DISPATCHED"
        assert fresh_model.normalization_message_id == 11

    @pytest.mark.asyncio
    async def test_recovers_embedding_batch_and_its_active_chunks(
        self,
        session_factory,
    ):
        video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)
        run = await repository.create_pipeline_run(video_id, "work-unit-v1")
        batch_id = uuid4()
        chunk = _make_chunk_work(
            run.id,
            chunk_index=0,
            enrichment_status="COMPLETED",
            embedding_status="DISPATCHED",
            embedding_batch_id=batch_id,
        )

        async with session_factory() as session:
            async with session.begin():
                session.add(chunk)
                session.add(
                    PipelineEmbeddingBatchModel(
                        batch_id=batch_id,
                        chunk_work_ids=[str(chunk.chunk_work_id)],
                        embedding_model_version="v001",
                        index_name="video-chunks",
                        status="DISPATCHED",
                        message_id=22,
                        dispatched_at=datetime.now(UTC) - timedelta(seconds=61),
                    )
                )

        recovered = await repository.recover_stale_dispatched_work(
            "EMBED_BATCH",
            visibility_timeout_sec=60,
        )

        async with session_factory() as session:
            batch = await session.get(PipelineEmbeddingBatchModel, batch_id)
            chunk_model = await session.get(PipelineChunkWorkModel, chunk.chunk_work_id)
        assert recovered == 1
        assert batch.status == "READY"
        assert batch.message_id is None
        assert chunk_model.embedding_status == "READY"
