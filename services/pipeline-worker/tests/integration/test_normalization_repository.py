from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from src.infra.db.models import (
    PipelineAudioPartModel,
    PipelineRunModel,
    VideoModel,
)
from src.infra.db.normalization_repository import NormalizationRepository
from src.infra.db.pipeline_dispatch_unit_of_work import (
    SqlAlchemyPipelineDispatchUnitOfWork,
)
from src.services.normalization_service import NormalizationPart
from src.services.pipeline_work_scheduler import PipelineWorkScheduler


class _RecordingPublisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[tuple[str, dict[str, object]]] = []

    async def send(self, session, queue_name, payload) -> int:
        del session
        self.messages.append((queue_name, payload))
        if self.fail:
            raise RuntimeError("pgmq send failed")
        return len(self.messages)


async def _seed_running_normalization(session_factory):
    video_id = uuid4()
    run_id = uuid4()
    async with session_factory() as session:
        async with session.begin():
            session.add(
                VideoModel(
                    id=video_id,
                    user_id=uuid4(),
                    title="normalization repository test",
                    category="test",
                    input_type="FILE",
                    storage_path="videos/source.mp4",
                    status="PROCESSING",
                )
            )
            session.add(
                PipelineRunModel(
                    id=run_id,
                    video_id=video_id,
                    pipeline_version="pipeline-v1",
                    normalization_status="RUNNING",
                    normalization_started_at=datetime.now(UTC),
                )
            )
    return video_id, run_id


def _repository(session_factory, publisher) -> NormalizationRepository:
    scheduler = PipelineWorkScheduler(
        SqlAlchemyPipelineDispatchUnitOfWork(session_factory, publisher)
    )
    return NormalizationRepository(
        session_factory=session_factory,
        publisher=publisher,
        scheduler=scheduler,
        stt_capacity=2,
    )


def _part() -> NormalizationPart:
    return NormalizationPart(
        part_index=0,
        start_ms=0,
        end_ms=60_000,
        storage_path="artifacts/video/run/audio-parts/part-000.flac",
    )


@pytest.mark.asyncio
async def test_part_insert_and_stt_publish_commit_together(session_factory) -> None:
    video_id, run_id = await _seed_running_normalization(session_factory)
    publisher = _RecordingPublisher()
    repository = _repository(session_factory, publisher)

    saved = await repository.complete_part_and_dispatch(
        video_id=video_id,
        pipeline_run_id=run_id,
        part=_part(),
        stt_model_version="chirp-v3",
        trace_id=uuid4(),
    )

    async with session_factory() as session:
        model = await session.scalar(select(PipelineAudioPartModel))
    assert saved is True
    assert model is not None
    assert model.status == "DISPATCHED"
    assert model.message_id == 1
    assert len(publisher.messages) == 1


@pytest.mark.asyncio
async def test_source_generation_is_bound_once(session_factory) -> None:
    video_id, run_id = await _seed_running_normalization(session_factory)
    repository = _repository(session_factory, _RecordingPublisher())

    assert await repository.bind_source_identity(
        video_id=video_id,
        pipeline_run_id=run_id,
        storage_path="videos/source.mp4",
        generation="123",
    )
    assert await repository.bind_source_identity(
        video_id=video_id,
        pipeline_run_id=run_id,
        storage_path="videos/source.mp4",
        generation="123",
    )
    with pytest.raises(RuntimeError, match="source generation changed"):
        await repository.bind_source_identity(
            video_id=video_id,
            pipeline_run_id=run_id,
            storage_path="videos/source.mp4",
            generation="456",
        )


@pytest.mark.asyncio
async def test_publish_failure_rolls_back_new_part(session_factory) -> None:
    video_id, run_id = await _seed_running_normalization(session_factory)
    repository = _repository(
        session_factory,
        _RecordingPublisher(fail=True),
    )

    with pytest.raises(RuntimeError, match="pgmq send failed"):
        await repository.complete_part_and_dispatch(
            video_id=video_id,
            pipeline_run_id=run_id,
            part=_part(),
            stt_model_version="chirp-v3",
            trace_id=uuid4(),
        )

    async with session_factory() as session:
        part_count = await session.scalar(
            select(func.count()).select_from(PipelineAudioPartModel)
        )
    assert part_count == 0


@pytest.mark.asyncio
async def test_completion_requires_exact_artifact_counts(session_factory) -> None:
    video_id, run_id = await _seed_running_normalization(session_factory)
    publisher = _RecordingPublisher()
    repository = _repository(session_factory, publisher)
    await repository.complete_part_and_dispatch(
        video_id=video_id,
        pipeline_run_id=run_id,
        part=_part(),
        stt_model_version="chirp-v3",
        trace_id=uuid4(),
    )

    with pytest.raises(
        RuntimeError,
        match="Cannot complete normalization with missing frame candidates",
    ):
        await repository.complete_normalization(
            video_id=video_id,
            pipeline_run_id=run_id,
            total_part_count=1,
            total_frame_count=1,
        )

    await repository.save_frame_candidate(
        video_id=video_id,
        pipeline_run_id=run_id,
        frame_index=0,
        timestamp_ms=30_000,
        frame_gcs_path="artifacts/video/run/frame-candidates/frame-00000.jpg",
    )

    with pytest.raises(
        RuntimeError,
        match="Cannot complete normalization with missing parts",
    ):
        await repository.complete_normalization(
            video_id=video_id,
            pipeline_run_id=run_id,
            total_part_count=2,
            total_frame_count=1,
        )

    completed = await repository.complete_normalization(
        video_id=video_id,
        pipeline_run_id=run_id,
        total_part_count=1,
        total_frame_count=1,
    )
    async with session_factory() as session:
        run = await session.get(PipelineRunModel, run_id)
    assert completed is True
    assert run is not None
    assert run.normalization_status == "COMPLETED"
    assert run.total_part_count == 1


@pytest.mark.asyncio
async def test_frame_candidate_rejects_changed_index_identity(session_factory) -> None:
    video_id, run_id = await _seed_running_normalization(session_factory)
    repository = _repository(session_factory, _RecordingPublisher())
    await repository.save_frame_candidate(
        video_id=video_id,
        pipeline_run_id=run_id,
        frame_index=0,
        timestamp_ms=30_000,
        frame_gcs_path="artifacts/video/run/frame-candidates/frame-00000.jpg",
    )

    with pytest.raises(
        RuntimeError,
        match="Frame candidate identity changed during retry",
    ):
        await repository.save_frame_candidate(
            video_id=video_id,
            pipeline_run_id=run_id,
            frame_index=0,
            timestamp_ms=45_000,
            frame_gcs_path="artifacts/video/run/frame-candidates/frame-00000-v2.jpg",
        )
