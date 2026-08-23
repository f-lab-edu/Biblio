from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select

from src.infra.ai.vision_adapter import VisionResult
from src.infra.db.enrichment_repository import SqlAlchemyEnrichmentRepository
from src.infra.db.models import (
    AssetModel,
    ChunkModel,
    PipelineChunkWorkModel,
    PipelineRunModel,
    VideoModel,
)
from src.schemas.messages import EnrichChunkMessage, MessageType


VIDEO_ID = UUID("10000000-0000-0000-0000-000000000001")
RUN_ID = UUID("20000000-0000-0000-0000-000000000001")
WORK_ID = UUID("30000000-0000-0000-0000-000000000001")
TRACE_ID = UUID("40000000-0000-0000-0000-000000000001")


class _Publisher:
    async def send(self, session, queue_name, payload) -> int:
        del session, queue_name, payload
        return 91


class _Scheduler:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
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
        if self.error is not None:
            raise self.error
        return 0


def _message() -> EnrichChunkMessage:
    return EnrichChunkMessage(
        message_type=MessageType.ENRICH_CHUNK,
        payload_version="v1",
        trace_id=TRACE_ID,
        attempt=1,
        pipeline_run_id=RUN_ID,
        video_id=VIDEO_ID,
        chunk_work_id=WORK_ID,
        chunk_index=2,
        chunking_version="chunk-v1",
        stt_model_version="chirp-3",
        issued_at=datetime.now(UTC),
    )


async def _seed(session_factory) -> None:
    async with session_factory() as session:
        async with session.begin():
            session.add(
                VideoModel(
                    id=VIDEO_ID,
                    user_id=UUID("50000000-0000-0000-0000-000000000001"),
                    title="video",
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
                )
            )
            await session.flush()
            session.add(
                PipelineChunkWorkModel(
                    chunk_work_id=WORK_ID,
                    pipeline_run_id=RUN_ID,
                    chunk_index=2,
                    text="원래 자막",
                    start_ms=10_000,
                    end_ms=20_000,
                    frame_ref="candidates/frame-002.jpg",
                    chunking_version="chunk-v1",
                    stt_model_version="chirp-3",
                    embedding_model_version="embed-v1",
                    index_name="video-index",
                    enrichment_status="RUNNING",
                    enrichment_attempt_count=1,
                    enrichment_message_id=71,
                    enrichment_started_at=datetime.now(UTC),
                )
            )


def _repository(session_factory, scheduler) -> SqlAlchemyEnrichmentRepository:
    return SqlAlchemyEnrichmentRepository(
        session_factory=session_factory,
        publisher=_Publisher(),
        scheduler=scheduler,
        enrichment_capacity=4,
        embedding_capacity=1,
    )


@pytest.mark.asyncio
async def test_completion_persists_result_and_advances_work_atomically(
    session_factory,
) -> None:
    await _seed(session_factory)
    scheduler = _Scheduler()
    repository = _repository(session_factory, scheduler)

    input_record = await repository.load_input(_message(), message_id=71)
    decision = await repository.complete(
        _message(),
        message_id=71,
        keyframe_ref="keyframes/chunk-000002.jpg",
        vision_result=VisionResult("caption", "ocr", "scene"),
        enriched_text="원래 자막 caption ocr scene",
    )

    async with session_factory() as session:
        work = await session.get(PipelineChunkWorkModel, WORK_ID)
        asset = await session.get(AssetModel, repository._asset_id(_message()))
        chunk = await session.get(ChunkModel, repository._chunk_id(_message()))

    assert input_record is not None
    assert input_record.frame_ref == "candidates/frame-002.jpg"
    assert decision.accepted is True
    assert work is not None and work.enrichment_status == "COMPLETED"
    assert work.embedding_status == "READY"
    assert work.chunk_id == chunk.id
    assert asset is not None and asset.storage_path == "keyframes/chunk-000002.jpg"
    assert chunk is not None and chunk.visual_caption == "caption"
    assert scheduler.calls == [("ENRICH_CHUNK", 4), ("EMBED_BATCH", 1)]


@pytest.mark.asyncio
async def test_deletion_during_vision_discards_all_result_rows(session_factory) -> None:
    await _seed(session_factory)
    repository = _repository(session_factory, _Scheduler())
    async with session_factory() as session:
        async with session.begin():
            video = await session.get(VideoModel, VIDEO_ID)
            assert video is not None
            video.status = "DELETING"

    decision = await repository.complete(
        _message(),
        message_id=71,
        keyframe_ref="keyframes/chunk-000002.jpg",
        vision_result=VisionResult("caption", "ocr", "scene"),
        enriched_text="enriched",
    )

    async with session_factory() as session:
        work = await session.get(PipelineChunkWorkModel, WORK_ID)
        result_count = await session.scalar(select(func.count()).select_from(ChunkModel))
        asset_count = await session.scalar(select(func.count()).select_from(AssetModel))

    assert decision == type(decision)(False, "video_deleting")
    assert work is not None and work.enrichment_status == "CANCELLED"
    assert result_count == 0
    assert asset_count == 0


@pytest.mark.asyncio
async def test_dispatch_failure_rolls_back_result_and_work_state(session_factory) -> None:
    await _seed(session_factory)
    repository = _repository(
        session_factory,
        _Scheduler(error=RuntimeError("dispatch failed")),
    )

    with pytest.raises(RuntimeError, match="dispatch failed"):
        await repository.complete(
            _message(),
            message_id=71,
            keyframe_ref="keyframes/chunk-000002.jpg",
            vision_result=VisionResult("caption", "ocr", "scene"),
            enriched_text="enriched",
        )

    async with session_factory() as session:
        work = await session.get(PipelineChunkWorkModel, WORK_ID)
        result_count = await session.scalar(select(func.count()).select_from(ChunkModel))
        asset_count = await session.scalar(select(func.count()).select_from(AssetModel))

    assert work is not None and work.enrichment_status == "RUNNING"
    assert work.embedding_status == "WAITING_ENRICHMENT"
    assert work.chunk_id is None
    assert result_count == 0
    assert asset_count == 0


@pytest.mark.asyncio
async def test_failure_during_deletion_cancels_running_work(session_factory) -> None:
    await _seed(session_factory)
    repository = _repository(session_factory, _Scheduler())
    async with session_factory() as session:
        async with session.begin():
            video = await session.get(VideoModel, VIDEO_ID)
            assert video is not None
            video.status = "DELETING"

    failed = await repository.fail(
        _message(),
        message_id=71,
        failure_code="VISION_UNAVAILABLE",
    )

    async with session_factory() as session:
        work = await session.get(PipelineChunkWorkModel, WORK_ID)

    assert failed is False
    assert work is not None and work.enrichment_status == "CANCELLED"
