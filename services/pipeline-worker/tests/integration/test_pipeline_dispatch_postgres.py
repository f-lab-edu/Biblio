import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infra.db.models import (
    Base,
    PipelineAudioPartModel,
    PipelineRunModel,
    VideoModel,
)
from src.infra.db.pipeline_dispatch_unit_of_work import (
    SqlAlchemyPipelineDispatchUnitOfWork,
)
from src.infra.queue.transactional_pgmq import TransactionalPGMQPublisher
from src.services.pipeline_work_scheduler import PipelineWorkScheduler


POSTGRES_URL = os.getenv("PIPELINE_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    POSTGRES_URL is None,
    reason="PIPELINE_TEST_POSTGRES_URL is required",
)

VIDEO_ID = UUID("00000000-0000-0000-0000-000000000011")
RUN_ID = UUID("10000000-0000-0000-0000-000000000011")
PART_ONE_ID = UUID("20000000-0000-0000-0000-000000000011")
PART_TWO_ID = UUID("20000000-0000-0000-0000-000000000012")
TRACE_ID = UUID("30000000-0000-0000-0000-000000000011")


class _SlowPublisher:
    def __init__(self) -> None:
        self.message_count = 0

    async def send(self, session, queue_name, payload) -> int:
        del session, queue_name, payload
        self.message_count += 1
        await asyncio.sleep(0.1)
        return self.message_count


class _RecordingPublisher:
    def __init__(self) -> None:
        self.message_count = 0

    async def send(self, session, queue_name, payload) -> int:
        del session, queue_name, payload
        self.message_count += 1
        return self.message_count


@pytest.mark.asyncio
async def test_advisory_lock_keeps_concurrent_dispatch_within_capacity() -> None:
    assert POSTGRES_URL is not None
    assert make_url(POSTGRES_URL).database == "biblio_pipeline_test"
    engine = create_async_engine(POSTGRES_URL)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            async with session.begin():
                session.add(
                    VideoModel(
                        id=VIDEO_ID,
                        user_id=UUID("40000000-0000-0000-0000-000000000011"),
                        title="postgres concurrency",
                        category="test",
                        input_type="FILE",
                        status="PROCESSING",
                    )
                )
                await session.flush()
                session.add(
                    PipelineRunModel(
                        id=RUN_ID,
                        video_id=VIDEO_ID,
                        pipeline_version="pipeline-v1",
                        normalization_status="COMPLETED",
                        normalization_completed=True,
                    )
                )
                await session.flush()
                session.add_all(
                    [
                        _part(PART_ONE_ID, part_index=0),
                        _part(PART_TWO_ID, part_index=1),
                    ]
                )

        publisher = _SlowPublisher()
        scheduler = PipelineWorkScheduler(
            SqlAlchemyPipelineDispatchUnitOfWork(session_factory, publisher)
        )

        results = await asyncio.gather(
            scheduler.dispatch_ready_work(
                "TRANSCRIBE_PART",
                capacity=1,
                trace_id=TRACE_ID,
            ),
            scheduler.dispatch_ready_work(
                "TRANSCRIBE_PART",
                capacity=1,
                trace_id=TRACE_ID,
            ),
        )

        async with session_factory() as session:
            statuses = list(
                await session.scalars(
                    select(PipelineAudioPartModel.status).order_by(
                        PipelineAudioPartModel.part_index
                    )
                )
            )

        assert sum(results) == 1
        assert publisher.message_count == 1
        assert statuses == ["DISPATCHED", "READY"]
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.asyncio
async def test_deletion_commit_prevents_waiting_dispatch_from_publishing() -> None:
    assert POSTGRES_URL is not None
    assert make_url(POSTGRES_URL).database == "biblio_pipeline_test"
    engine = create_async_engine(POSTGRES_URL)
    deletion_locked = asyncio.Event()
    allow_deletion_commit = asyncio.Event()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        await _seed_transcription_work(session_factory, part_ids=(PART_ONE_ID,))
        deletion_task = asyncio.create_task(
            _mark_video_deleting(
                session_factory,
                deletion_locked=deletion_locked,
                allow_commit=allow_deletion_commit,
            )
        )
        await deletion_locked.wait()

        publisher = _RecordingPublisher()
        scheduler = PipelineWorkScheduler(
            SqlAlchemyPipelineDispatchUnitOfWork(session_factory, publisher)
        )
        dispatch_task = asyncio.create_task(
            scheduler.dispatch_ready_work(
                "TRANSCRIBE_PART",
                capacity=1,
                trace_id=TRACE_ID,
            )
        )
        await asyncio.sleep(0.05)
        allow_deletion_commit.set()

        await deletion_task
        dispatched_count = await dispatch_task

        async with session_factory() as session:
            part_status = await session.scalar(
                select(PipelineAudioPartModel.status).where(
                    PipelineAudioPartModel.audio_part_id == PART_ONE_ID
                )
            )

        assert dispatched_count == 0
        assert publisher.message_count == 0
        assert part_status == "READY"
    finally:
        allow_deletion_commit.set()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _part(part_id: UUID, *, part_index: int) -> PipelineAudioPartModel:
    return PipelineAudioPartModel(
        audio_part_id=part_id,
        pipeline_run_id=RUN_ID,
        part_index=part_index,
        start_ms=part_index * 1_000,
        end_ms=(part_index + 1) * 1_000,
        audio_gcs_path=f"audio/{part_id}.flac",
        stt_model_version="chirp_2",
        status="READY",
        ready_at=datetime(2026, 8, 21, tzinfo=UTC),
    )


async def _seed_transcription_work(
    session_factory,
    *,
    part_ids: tuple[UUID, ...],
) -> None:
    async with session_factory() as session:
        async with session.begin():
            session.add(
                VideoModel(
                    id=VIDEO_ID,
                    user_id=UUID("40000000-0000-0000-0000-000000000011"),
                    title="postgres deletion race",
                    category="test",
                    input_type="FILE",
                    status="PROCESSING",
                )
            )
            await session.flush()
            session.add(
                PipelineRunModel(
                    id=RUN_ID,
                    video_id=VIDEO_ID,
                    pipeline_version="pipeline-v1",
                    normalization_status="COMPLETED",
                    normalization_completed=True,
                )
            )
            await session.flush()
            session.add_all(
                [_part(part_id, part_index=index) for index, part_id in enumerate(part_ids)]
            )


async def _mark_video_deleting(
    session_factory,
    *,
    deletion_locked: asyncio.Event,
    allow_commit: asyncio.Event,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            video = await session.get(VideoModel, VIDEO_ID, with_for_update=True)
            assert video is not None
            video.status = "DELETING"
            await session.flush()
            deletion_locked.set()
            await allow_commit.wait()


@pytest.mark.asyncio
async def test_pgmq_send_rolls_back_with_database_transaction() -> None:
    assert POSTGRES_URL is not None
    assert make_url(POSTGRES_URL).database == "biblio_pipeline_test"
    engine = create_async_engine(POSTGRES_URL)
    queue_name = "pipeline_dispatch_tx_test"
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pgmq.drop_queue(:queue_name)"),
                    {"queue_name": queue_name},
                )
                await session.execute(
                    text("SELECT pgmq.create(:queue_name)"),
                    {"queue_name": queue_name},
                )

        publisher = TransactionalPGMQPublisher()
        with pytest.raises(RuntimeError, match="force rollback"):
            async with session_factory() as session:
                async with session.begin():
                    await publisher.send(
                        session,
                        queue_name,
                        {"message_type": "NORMALIZE_VIDEO"},
                    )
                    raise RuntimeError("force rollback")

        async with session_factory() as session:
            queue_length = await session.scalar(
                text("SELECT queue_length FROM pgmq.metrics(:queue_name)"),
                {"queue_name": queue_name},
            )
        assert queue_length == 0
    finally:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pgmq.drop_queue(:queue_name)"),
                    {"queue_name": queue_name},
                )
        await engine.dispose()


@pytest.mark.asyncio
async def test_unacked_pgmq_message_is_redelivered_with_same_id() -> None:
    assert POSTGRES_URL is not None
    assert make_url(POSTGRES_URL).database == "biblio_pipeline_test"
    engine = create_async_engine(POSTGRES_URL)
    queue_name = "pipeline_redelivery_test"
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pgmq.drop_queue(:queue_name)"),
                    {"queue_name": queue_name},
                )
                await session.execute(
                    text("SELECT pgmq.create(:queue_name)"),
                    {"queue_name": queue_name},
                )
                message_id = await TransactionalPGMQPublisher().send(
                    session,
                    queue_name,
                    {"message_type": "NORMALIZE_VIDEO"},
                )

        first_delivery = await _read_pgmq_message(session_factory, queue_name)
        second_delivery = await _read_pgmq_message(session_factory, queue_name)

        assert first_delivery is not None
        assert second_delivery is not None
        assert first_delivery.msg_id == message_id
        assert second_delivery.msg_id == message_id
        assert second_delivery.read_ct == first_delivery.read_ct + 1
    finally:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pgmq.drop_queue(:queue_name)"),
                    {"queue_name": queue_name},
                )
        await engine.dispose()


async def _read_pgmq_message(session_factory, queue_name: str):
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                text("SELECT * FROM pgmq.read(:queue_name, 0, 1)"),
                {"queue_name": queue_name},
            )
            return result.first()
