from datetime import UTC, datetime
from uuid import UUID

import pytest
from loguru import logger
from sqlalchemy import select

from src.infra.db.models import (
    PipelineAudioPartModel,
    PipelineChunkWorkModel,
    PipelineEmbeddingBatchModel,
    PipelineRunModel,
    PipelineStageScheduleModel,
    VideoModel,
)
from src.infra.db.pipeline_dispatch_unit_of_work import (
    SqlAlchemyPipelineDispatchUnitOfWork,
)
from src.services.pipeline_work_scheduler import PipelineWorkScheduler


VIDEO_A = UUID("00000000-0000-0000-0000-00000000000a")
VIDEO_B = UUID("00000000-0000-0000-0000-00000000000b")
RUN_A = UUID("10000000-0000-0000-0000-00000000000a")
RUN_B = UUID("10000000-0000-0000-0000-00000000000b")
PART_A1 = UUID("20000000-0000-0000-0000-000000000001")
PART_A2 = UUID("20000000-0000-0000-0000-000000000002")
PART_B1 = UUID("20000000-0000-0000-0000-000000000003")
CHUNK_A1 = UUID("50000000-0000-0000-0000-000000000001")
BATCH_A = UUID("60000000-0000-0000-0000-000000000001")
TRACE_ID = UUID("40000000-0000-0000-0000-000000000001")


class _RecordingPublisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[tuple[str, dict[str, object]]] = []

    async def send(self, session, queue_name, payload) -> int:
        del session
        self.messages.append((queue_name, payload))
        if self.fail:
            raise RuntimeError("pgmq send failed")
        return 100 + len(self.messages)


def _video(video_id: UUID, *, status: str = "PROCESSING") -> VideoModel:
    return VideoModel(
        id=video_id,
        user_id=UUID("30000000-0000-0000-0000-000000000001"),
        title=f"video-{video_id}",
        category="test",
        input_type="FILE",
        status=status,
    )


def _run(run_id: UUID, video_id: UUID) -> PipelineRunModel:
    return PipelineRunModel(
        id=run_id,
        video_id=video_id,
        pipeline_version="pipeline-v1",
        normalization_status="COMPLETED",
        normalization_completed=True,
    )


def _part(
    part_id: UUID,
    run_id: UUID,
    part_index: int,
) -> PipelineAudioPartModel:
    return PipelineAudioPartModel(
        audio_part_id=part_id,
        pipeline_run_id=run_id,
        part_index=part_index,
        start_ms=part_index * 1_000,
        end_ms=(part_index + 1) * 1_000,
        audio_gcs_path=f"audio/{part_id}.flac",
        stt_model_version="chirp_2",
        status="READY",
        ready_at=datetime(2026, 8, 21, tzinfo=UTC),
    )


def _chunk(
    chunk_id: UUID,
    run_id: UUID,
    batch_id: UUID,
) -> PipelineChunkWorkModel:
    return PipelineChunkWorkModel(
        chunk_work_id=chunk_id,
        pipeline_run_id=run_id,
        chunk_index=0,
        text="enriched chunk",
        start_ms=0,
        end_ms=1_000,
        chunking_version="v1",
        stt_model_version="chirp_2",
        embedding_model_version="v001",
        index_name="video-chunks",
        enrichment_status="COMPLETED",
        embedding_status="READY",
        embedding_batch_id=batch_id,
        embedding_ready_at=datetime(2026, 8, 21, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_dispatches_fairly_and_persists_message_ids(session_factory) -> None:
    async with session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    _video(VIDEO_A),
                    _video(VIDEO_B),
                    _run(RUN_A, VIDEO_A),
                    _run(RUN_B, VIDEO_B),
                    _part(PART_A1, RUN_A, 0),
                    _part(PART_A2, RUN_A, 1),
                    _part(PART_B1, RUN_B, 0),
                ]
            )

    publisher = _RecordingPublisher()
    scheduler = PipelineWorkScheduler(
        SqlAlchemyPipelineDispatchUnitOfWork(session_factory, publisher)
    )

    records: list[dict] = []
    sink_id = logger.add(lambda message: records.append(message.record))
    try:
        dispatched = await scheduler.dispatch_ready_work(
            "TRANSCRIBE_PART",
            capacity=2,
            trace_id=TRACE_ID,
        )
    finally:
        logger.remove(sink_id)

    assert dispatched == 2
    assert [payload["audio_part_id"] for _, payload in publisher.messages] == [
        str(PART_A1),
        str(PART_B1),
    ]
    assert all(
        payload["trace_id"] == str(TRACE_ID) for _, payload in publisher.messages
    )
    async with session_factory() as session:
        parts = {
            model.audio_part_id: model
            for model in await session.scalars(select(PipelineAudioPartModel))
        }
        schedules = list(await session.scalars(select(PipelineStageScheduleModel)))

    assert parts[PART_A1].status == "DISPATCHED"
    assert parts[PART_A1].message_id == 101
    assert parts[PART_B1].status == "DISPATCHED"
    assert parts[PART_B1].message_id == 102
    assert parts[PART_A2].status == "READY"
    assert {schedule.pipeline_run_id for schedule in schedules} == {RUN_A, RUN_B}
    dispatch_events = [
        record
        for record in records
        if record["extra"].get("event_name") == "pipeline.work.dispatched"
    ]
    assert [record["extra"]["work_id"] for record in dispatch_events] == [
        str(PART_A1),
        str(PART_B1),
    ]
    assert all(record["extra"]["log_schema_version"] == 2 for record in dispatch_events)


@pytest.mark.asyncio
async def test_publisher_failure_rolls_back_work_state(session_factory) -> None:
    async with session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    _video(VIDEO_A),
                    PipelineRunModel(
                        id=RUN_A,
                        video_id=VIDEO_A,
                        pipeline_version="pipeline-v1",
                        normalization_status="READY",
                        normalization_ready_at=datetime(2026, 8, 21, tzinfo=UTC),
                    ),
                ]
            )

    scheduler = PipelineWorkScheduler(
        SqlAlchemyPipelineDispatchUnitOfWork(
            session_factory,
            _RecordingPublisher(fail=True),
        )
    )

    with pytest.raises(RuntimeError, match="pgmq send failed"):
        await scheduler.dispatch_ready_work(
            "NORMALIZE_VIDEO",
            capacity=1,
            trace_id=TRACE_ID,
        )

    async with session_factory() as session:
        run = await session.get(PipelineRunModel, RUN_A)
        schedules = list(await session.scalars(select(PipelineStageScheduleModel)))

    assert run is not None
    assert run.normalization_status == "READY"
    assert run.normalization_attempt_count == 0
    assert run.normalization_message_id is None
    assert schedules == []


@pytest.mark.asyncio
async def test_counts_dispatched_work_before_filling_capacity(session_factory) -> None:
    in_flight_part = _part(PART_A1, RUN_A, 0)
    in_flight_part.status = "DISPATCHED"
    async with session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    _video(VIDEO_A),
                    _video(VIDEO_B),
                    _run(RUN_A, VIDEO_A),
                    _run(RUN_B, VIDEO_B),
                    in_flight_part,
                    _part(PART_A2, RUN_A, 1),
                    _part(PART_B1, RUN_B, 0),
                ]
            )

    publisher = _RecordingPublisher()
    scheduler = PipelineWorkScheduler(
        SqlAlchemyPipelineDispatchUnitOfWork(session_factory, publisher)
    )

    dispatched = await scheduler.dispatch_ready_work(
        "TRANSCRIBE_PART",
        capacity=2,
        trace_id=TRACE_ID,
    )

    assert dispatched == 1
    assert len(publisher.messages) == 1


@pytest.mark.asyncio
async def test_does_not_publish_the_same_work_twice(session_factory) -> None:
    async with session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    _video(VIDEO_A),
                    _run(RUN_A, VIDEO_A),
                    _part(PART_A1, RUN_A, 0),
                ]
            )

    publisher = _RecordingPublisher()
    scheduler = PipelineWorkScheduler(
        SqlAlchemyPipelineDispatchUnitOfWork(session_factory, publisher)
    )

    first_dispatch = await scheduler.dispatch_ready_work(
        "TRANSCRIBE_PART",
        capacity=2,
        trace_id=TRACE_ID,
    )
    second_dispatch = await scheduler.dispatch_ready_work(
        "TRANSCRIBE_PART",
        capacity=2,
        trace_id=TRACE_ID,
    )

    assert first_dispatch == 1
    assert second_dispatch == 0
    assert len(publisher.messages) == 1


@pytest.mark.asyncio
async def test_dispatches_embedding_batch_and_marks_its_chunks(session_factory) -> None:
    async with session_factory() as session:
        async with session.begin():
            session.add(_video(VIDEO_A))
            await session.flush()
            session.add(_run(RUN_A, VIDEO_A))
            await session.flush()
            session.add(
                PipelineEmbeddingBatchModel(
                    batch_id=BATCH_A,
                    chunk_work_ids=[str(CHUNK_A1)],
                    embedding_model_version="v001",
                    index_name="video-chunks",
                    status="READY",
                    ready_at=datetime(2026, 8, 21, tzinfo=UTC),
                )
            )
            await session.flush()
            session.add(_chunk(CHUNK_A1, RUN_A, BATCH_A))

    publisher = _RecordingPublisher()
    scheduler = PipelineWorkScheduler(
        SqlAlchemyPipelineDispatchUnitOfWork(session_factory, publisher)
    )

    dispatched = await scheduler.dispatch_ready_work(
        "EMBED_BATCH",
        capacity=1,
        trace_id=TRACE_ID,
    )

    async with session_factory() as session:
        batch = await session.get(PipelineEmbeddingBatchModel, BATCH_A)
        chunk = await session.get(PipelineChunkWorkModel, CHUNK_A1)

    assert dispatched == 1
    assert len(publisher.messages) == 1
    assert batch is not None
    assert batch.status == "DISPATCHED"
    assert batch.message_id == 101
    assert chunk is not None
    assert chunk.embedding_status == "DISPATCHED"
    assert chunk.embedding_attempt_count == 1


@pytest.mark.asyncio
async def test_does_not_dispatch_work_for_deleting_video(session_factory) -> None:
    async with session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    _video(VIDEO_A, status="DELETING"),
                    _run(RUN_A, VIDEO_A),
                    _part(PART_A1, RUN_A, 0),
                ]
            )

    publisher = _RecordingPublisher()
    scheduler = PipelineWorkScheduler(
        SqlAlchemyPipelineDispatchUnitOfWork(session_factory, publisher)
    )

    dispatched = await scheduler.dispatch_ready_work(
        "TRANSCRIBE_PART",
        capacity=1,
        trace_id=TRACE_ID,
    )

    assert dispatched == 0
    assert publisher.messages == []
