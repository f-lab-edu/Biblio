from datetime import UTC, datetime
from uuid import UUID

import pytest

from src.infra.db.models import (
    PipelineAudioPartModel,
    PipelineChunkWorkModel,
    PipelineEmbeddingBatchModel,
    PipelineRunModel,
    VideoModel,
)
from src.infra.db.stage_message_guard import SqlAlchemyStageMessageClaimer
from src.schemas.messages import (
    EmbedBatchMessage,
    MessageType,
    NormalizeVideoMessage,
    TranscribePartMessage,
)


VIDEO_ID = UUID("00000000-0000-0000-0000-000000000001")
VIDEO_B = UUID("00000000-0000-0000-0000-000000000002")
RUN_ID = UUID("10000000-0000-0000-0000-000000000001")
RUN_B = UUID("10000000-0000-0000-0000-000000000002")
PART_ID = UUID("20000000-0000-0000-0000-000000000001")
BATCH_ID = UUID("50000000-0000-0000-0000-000000000001")
CHUNK_ID = UUID("60000000-0000-0000-0000-000000000001")
CHUNK_B = UUID("60000000-0000-0000-0000-000000000002")
TRACE_ID = UUID("30000000-0000-0000-0000-000000000001")


def _video(*, status: str = "PROCESSING") -> VideoModel:
    return VideoModel(
        id=VIDEO_ID,
        user_id=UUID("40000000-0000-0000-0000-000000000001"),
        title="guard test",
        category="test",
        input_type="FILE",
        status=status,
    )


def _run(
    *,
    status: str = "DISPATCHED",
    message_id: int = 10,
    is_active: bool = True,
) -> PipelineRunModel:
    return PipelineRunModel(
        id=RUN_ID,
        video_id=VIDEO_ID,
        pipeline_version="pipeline-v1",
        is_active=is_active,
        status="RUNNING" if is_active else "SUPERSEDED",
        normalization_status=status,
        normalization_message_id=message_id,
    )


def _normalize_message() -> NormalizeVideoMessage:
    return NormalizeVideoMessage(
        message_type=MessageType.NORMALIZE_VIDEO,
        payload_version="v1",
        trace_id=TRACE_ID,
        attempt=1,
        pipeline_run_id=RUN_ID,
        video_id=VIDEO_ID,
        pipeline_version="pipeline-v1",
        issued_at=datetime(2026, 8, 21, tzinfo=UTC),
    )


def _transcribe_message() -> TranscribePartMessage:
    return TranscribePartMessage(
        message_type=MessageType.TRANSCRIBE_PART,
        payload_version="v1",
        trace_id=TRACE_ID,
        attempt=1,
        pipeline_run_id=RUN_ID,
        video_id=VIDEO_ID,
        audio_part_id=PART_ID,
        part_index=0,
        stt_model_version="chirp_2",
        issued_at=datetime(2026, 8, 21, tzinfo=UTC),
    )


def _embed_message() -> EmbedBatchMessage:
    return EmbedBatchMessage(
        message_type=MessageType.EMBED_BATCH,
        payload_version="v1",
        trace_id=TRACE_ID,
        attempt=1,
        batch_id=BATCH_ID,
        embedding_model_version="v001",
        index_name="video-chunks",
        issued_at=datetime(2026, 8, 21, tzinfo=UTC),
    )


class TestNormalizeMessageInspection:
    @pytest.mark.asyncio
    async def test_accepts_current_dispatched_message(self, session_factory) -> None:
        async with session_factory() as session:
            async with session.begin():
                session.add_all([_video(), _run()])

        decision = await SqlAlchemyStageMessageClaimer(
            session_factory
        ).claim_for_execution(_normalize_message(), message_id=10)

        assert decision.should_execute is True
        assert decision.reason == "executable"
        async with session_factory() as session:
            run = await session.get(PipelineRunModel, RUN_ID)
        assert run is not None
        assert run.normalization_status == "RUNNING"
        assert run.normalization_started_at is not None

    @pytest.mark.asyncio
    async def test_rejects_message_when_db_has_newer_message_id(
        self,
        session_factory,
    ) -> None:
        async with session_factory() as session:
            async with session.begin():
                session.add_all([_video(), _run(message_id=11)])

        decision = await SqlAlchemyStageMessageClaimer(
            session_factory
        ).claim_for_execution(_normalize_message(), message_id=10)

        assert decision.should_execute is False
        assert decision.reason == "stale_message_id"

    @pytest.mark.asyncio
    async def test_rejects_completed_message_redelivery(self, session_factory) -> None:
        async with session_factory() as session:
            async with session.begin():
                session.add_all([_video(), _run(status="COMPLETED")])

        decision = await SqlAlchemyStageMessageClaimer(
            session_factory
        ).claim_for_execution(_normalize_message(), message_id=10)

        assert decision.should_execute is False
        assert decision.reason == "terminal_or_not_dispatched"

    @pytest.mark.asyncio
    async def test_cancels_dispatched_work_when_video_is_deleting(
        self,
        session_factory,
    ) -> None:
        async with session_factory() as session:
            async with session.begin():
                session.add_all([_video(status="DELETING"), _run()])

        decision = await SqlAlchemyStageMessageClaimer(
            session_factory
        ).claim_for_execution(_normalize_message(), message_id=10)

        async with session_factory() as session:
            run = await session.get(PipelineRunModel, RUN_ID)
        assert decision.should_execute is False
        assert decision.reason == "video_deleting"
        assert run is not None
        assert run.normalization_status == "CANCELLED"


class TestTranscriptionMessageInspection:
    @pytest.mark.asyncio
    async def test_accepts_current_running_redelivery(self, session_factory) -> None:
        async with session_factory() as session:
            async with session.begin():
                session.add_all(
                    [
                        _video(),
                        _run(status="COMPLETED"),
                        PipelineAudioPartModel(
                            audio_part_id=PART_ID,
                            pipeline_run_id=RUN_ID,
                            part_index=0,
                            start_ms=0,
                            end_ms=1_000,
                            audio_gcs_path="audio/part.flac",
                            stt_model_version="chirp_2",
                            status="RUNNING",
                            message_id=20,
                        ),
                    ]
                )

        decision = await SqlAlchemyStageMessageClaimer(
            session_factory
        ).claim_for_execution(_transcribe_message(), message_id=20)

        assert decision.should_execute is True
        assert decision.reason == "executable"


class TestEmbeddingBatchClaim:
    @pytest.mark.asyncio
    async def test_marks_batch_and_chunks_running(self, session_factory) -> None:
        async with session_factory() as session:
            async with session.begin():
                session.add(_video())
                await session.flush()
                session.add(_run(status="COMPLETED"))
                await session.flush()
                session.add(
                    PipelineEmbeddingBatchModel(
                        batch_id=BATCH_ID,
                        chunk_work_ids=[str(CHUNK_ID)],
                        embedding_model_version="v001",
                        index_name="video-chunks",
                        status="DISPATCHED",
                        message_id=30,
                    )
                )
                await session.flush()
                session.add(
                    PipelineChunkWorkModel(
                        chunk_work_id=CHUNK_ID,
                        pipeline_run_id=RUN_ID,
                        chunk_index=0,
                        text="enriched chunk",
                        start_ms=0,
                        end_ms=1_000,
                        chunking_version="v1",
                        stt_model_version="chirp_2",
                        embedding_model_version="v001",
                        index_name="video-chunks",
                        enrichment_status="COMPLETED",
                        embedding_status="DISPATCHED",
                        embedding_batch_id=BATCH_ID,
                    )
                )

        decision = await SqlAlchemyStageMessageClaimer(
            session_factory
        ).claim_for_execution(_embed_message(), message_id=30)

        async with session_factory() as session:
            batch = await session.get(PipelineEmbeddingBatchModel, BATCH_ID)
            chunk = await session.get(PipelineChunkWorkModel, CHUNK_ID)

        assert decision.should_execute is True
        assert batch is not None
        assert batch.status == "RUNNING"
        assert batch.started_at is not None
        assert chunk is not None
        assert chunk.embedding_status == "RUNNING"
        assert chunk.embedding_started_at is not None

    @pytest.mark.asyncio
    async def test_mixed_batch_cancels_only_deleting_video_work(
        self,
        session_factory,
    ) -> None:
        video_b = _video(status="DELETING")
        video_b.id = VIDEO_B
        run_b = _run(status="COMPLETED")
        run_b.id = RUN_B
        run_b.video_id = VIDEO_B
        async with session_factory() as session:
            async with session.begin():
                session.add_all([_video(), video_b])
                await session.flush()
                session.add_all([_run(status="COMPLETED"), run_b])
                await session.flush()
                session.add(
                    PipelineEmbeddingBatchModel(
                        batch_id=BATCH_ID,
                        chunk_work_ids=[str(CHUNK_ID), str(CHUNK_B)],
                        embedding_model_version="v001",
                        index_name="video-chunks",
                        status="DISPATCHED",
                        message_id=30,
                    )
                )
                await session.flush()
                session.add_all(
                    [
                        PipelineChunkWorkModel(
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
                            embedding_status="DISPATCHED",
                            embedding_batch_id=BATCH_ID,
                        )
                        for chunk_id, run_id in (
                            (CHUNK_ID, RUN_ID),
                            (CHUNK_B, RUN_B),
                        )
                    ]
                )

        decision = await SqlAlchemyStageMessageClaimer(
            session_factory
        ).claim_for_execution(_embed_message(), message_id=30)

        async with session_factory() as session:
            batch = await session.get(PipelineEmbeddingBatchModel, BATCH_ID)
            active_chunk = await session.get(PipelineChunkWorkModel, CHUNK_ID)
            deleted_chunk = await session.get(PipelineChunkWorkModel, CHUNK_B)

        assert decision.should_execute is True
        assert batch is not None and batch.status == "RUNNING"
        assert active_chunk is not None
        assert active_chunk.embedding_status == "RUNNING"
        assert deleted_chunk is not None
        assert deleted_chunk.embedding_status == "CANCELLED"
