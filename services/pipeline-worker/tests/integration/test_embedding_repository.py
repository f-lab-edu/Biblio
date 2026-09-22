from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select

from src.infra.db.embedding_repository import SqlAlchemyEmbeddingRepository
from src.infra.db.models import (
    ChunkModel,
    PipelineAudioPartModel,
    PipelineChunkWorkModel,
    PipelineEmbeddingBatchModel,
    PipelineRunModel,
    VectorIndexEntryModel,
    VideoModel,
)
from src.schemas.messages import EmbedBatchMessage, MessageType


VIDEO_A = UUID("10000000-0000-0000-0000-000000000001")
VIDEO_B = UUID("10000000-0000-0000-0000-000000000002")
RUN_A = UUID("20000000-0000-0000-0000-000000000001")
RUN_B = UUID("20000000-0000-0000-0000-000000000002")
WORK_A = UUID("30000000-0000-0000-0000-000000000001")
WORK_B = UUID("30000000-0000-0000-0000-000000000002")
CHUNK_A = UUID("40000000-0000-0000-0000-000000000001")
CHUNK_B = UUID("40000000-0000-0000-0000-000000000002")
PART_A = UUID("50000000-0000-0000-0000-000000000001")
PART_B = UUID("50000000-0000-0000-0000-000000000002")
BATCH_ID = UUID("60000000-0000-0000-0000-000000000001")
TRACE_ID = UUID("70000000-0000-0000-0000-000000000001")


class _Publisher:
    async def send(self, session, queue_name, payload) -> int:
        del session, queue_name, payload
        return 92


class _Scheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def dispatch_in_transaction(
        self,
        transaction,
        stage,
        capacity,
        *,
        trace_id,
    ) -> int:
        del transaction, trace_id
        self.calls.append((stage, capacity))
        return 0


def _message() -> EmbedBatchMessage:
    return EmbedBatchMessage(
        message_type=MessageType.EMBED_BATCH,
        payload_version="v1",
        trace_id=TRACE_ID,
        attempt=1,
        batch_id=BATCH_ID,
        embedding_model_version="embed-v1",
        index_name="video-index",
        issued_at=datetime.now(UTC),
    )


def _video(video_id: UUID) -> VideoModel:
    return VideoModel(
        id=video_id,
        user_id=UUID("80000000-0000-0000-0000-000000000001"),
        title=f"video-{video_id}",
        category="test",
        input_type="FILE",
        status="PROCESSING",
    )


def _run(run_id: UUID, video_id: UUID) -> PipelineRunModel:
    return PipelineRunModel(
        id=run_id,
        video_id=video_id,
        pipeline_version="pipeline-v1",
        normalization_status="COMPLETED",
        normalization_completed=True,
        transcript_completed=True,
        assembly_completed=True,
        total_part_count=1,
        next_part_index=1,
        pending_words=[],
        chunk_buffer=[],
    )


def _part(part_id: UUID, run_id: UUID) -> PipelineAudioPartModel:
    return PipelineAudioPartModel(
        audio_part_id=part_id,
        pipeline_run_id=run_id,
        part_index=0,
        start_ms=0,
        end_ms=1_000,
        audio_gcs_path=f"audio/{part_id}.flac",
        stt_model_version="chirp-3",
        status="COMPLETED",
    )


def _chunk(chunk_id: UUID, video_id: UUID, chunk_index: int) -> ChunkModel:
    return ChunkModel(
        id=chunk_id,
        video_id=video_id,
        chunk_index=chunk_index,
        text="transcript",
        enriched_text=f"enriched-{chunk_index}",
        start_ms=0,
        end_ms=1_000,
        chunking_version="chunk-v1",
        stt_model_version="chirp-3",
        embedding_model_version="embed-v1",
    )


def _work(
    work_id: UUID,
    run_id: UUID,
    chunk_id: UUID,
    chunk_index: int,
) -> PipelineChunkWorkModel:
    return PipelineChunkWorkModel(
        chunk_work_id=work_id,
        pipeline_run_id=run_id,
        chunk_index=chunk_index,
        text="transcript",
        start_ms=0,
        end_ms=1_000,
        chunking_version="chunk-v1",
        stt_model_version="chirp-3",
        embedding_model_version="embed-v1",
        index_name="video-index",
        chunk_id=chunk_id,
        enrichment_status="COMPLETED",
        embedding_status="RUNNING",
        embedding_batch_id=BATCH_ID,
        embedding_attempt_count=1,
    )


async def _seed(session_factory, *, include_video_b: bool = False) -> None:
    work_ids = [str(WORK_A), str(WORK_B)] if include_video_b else [str(WORK_A)]
    async with session_factory() as session:
        async with session.begin():
            session.add_all([_video(VIDEO_A), *([_video(VIDEO_B)] if include_video_b else [])])
            await session.flush()
            session.add_all(
                [
                    _run(RUN_A, VIDEO_A),
                    *([_run(RUN_B, VIDEO_B)] if include_video_b else []),
                ]
            )
            session.add(
                PipelineEmbeddingBatchModel(
                    batch_id=BATCH_ID,
                    chunk_work_ids=work_ids,
                    embedding_model_version="embed-v1",
                    index_name="video-index",
                    status="RUNNING",
                    attempt_count=1,
                    message_id=91,
                )
            )
            await session.flush()
            session.add_all(
                [
                    _part(PART_A, RUN_A),
                    _chunk(CHUNK_A, VIDEO_A, 0),
                    _work(WORK_A, RUN_A, CHUNK_A, 0),
                ]
            )
            if include_video_b:
                session.add_all(
                    [
                        _part(PART_B, RUN_B),
                        _chunk(CHUNK_B, VIDEO_B, 0),
                        _work(WORK_B, RUN_B, CHUNK_B, 0),
                    ]
                )


def _repository(session_factory, scheduler) -> SqlAlchemyEmbeddingRepository:
    return SqlAlchemyEmbeddingRepository(
        session_factory=session_factory,
        publisher=_Publisher(),
        scheduler=scheduler,
        embedding_capacity=1,
    )


@pytest.mark.asyncio
async def test_completion_stores_vector_and_marks_video_ready(session_factory) -> None:
    await _seed(session_factory)
    scheduler = _Scheduler()
    repository = _repository(session_factory, scheduler)

    input_record = await repository.load_input(_message(), message_id=91)
    assert input_record is not None
    decision = await repository.complete(
        _message(),
        message_id=91,
        input_record=input_record,
        vectors=[[0.1, 0.2]],
    )

    async with session_factory() as session:
        work = await session.get(PipelineChunkWorkModel, WORK_A)
        batch = await session.get(PipelineEmbeddingBatchModel, BATCH_ID)
        run = await session.get(PipelineRunModel, RUN_A)
        video = await session.get(VideoModel, VIDEO_A)
        vector = await session.get(
            VectorIndexEntryModel,
            {"index_name": "video-index", "chunk_id": CHUNK_A},
        )

    assert decision.stored_count == 1
    assert work is not None and work.embedding_status == "COMPLETED"
    assert batch is not None and batch.status == "COMPLETED"
    assert run is not None and run.status == "COMPLETED" and run.is_active is False
    assert video is not None and video.status == "READY"
    assert vector is not None and vector.embedding_vector == pytest.approx([0.1, 0.2])
    assert scheduler.calls == [("EMBED_BATCH", 1)]


@pytest.mark.asyncio
async def test_deletion_during_mixed_batch_discards_only_deleted_video(
    session_factory,
) -> None:
    await _seed(session_factory, include_video_b=True)
    repository = _repository(session_factory, _Scheduler())
    input_record = await repository.load_input(_message(), message_id=91)
    assert input_record is not None and len(input_record.chunks) == 2
    async with session_factory() as session:
        async with session.begin():
            video = await session.get(VideoModel, VIDEO_B)
            assert video is not None
            video.status = "DELETING"

    decision = await repository.complete(
        _message(),
        message_id=91,
        input_record=input_record,
        vectors=[[0.1, 0.2], [0.3, 0.4]],
    )

    async with session_factory() as session:
        work_a = await session.get(PipelineChunkWorkModel, WORK_A)
        work_b = await session.get(PipelineChunkWorkModel, WORK_B)
        vector_count = await session.scalar(
            select(func.count()).select_from(VectorIndexEntryModel)
        )

    assert decision.stored_count == 1
    assert decision.discarded_count == 1
    assert work_a is not None and work_a.embedding_status == "COMPLETED"
    assert work_b is not None and work_b.embedding_status == "CANCELLED"
    assert vector_count == 1


@pytest.mark.asyncio
async def test_missing_pipeline_condition_does_not_mark_video_ready(
    session_factory,
) -> None:
    await _seed(session_factory)
    async with session_factory() as session:
        async with session.begin():
            run = await session.get(PipelineRunModel, RUN_A)
            assert run is not None
            run.transcript_completed = False
    repository = _repository(session_factory, _Scheduler())
    input_record = await repository.load_input(_message(), message_id=91)
    assert input_record is not None

    await repository.complete(
        _message(),
        message_id=91,
        input_record=input_record,
        vectors=[[0.1, 0.2]],
    )

    async with session_factory() as session:
        run = await session.get(PipelineRunModel, RUN_A)
        video = await session.get(VideoModel, VIDEO_A)

    assert run is not None and run.status == "RUNNING" and run.is_active is True
    assert video is not None and video.status == "PROCESSING"
