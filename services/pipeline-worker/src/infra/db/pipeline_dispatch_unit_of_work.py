from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import case, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.models import (
    PipelineAudioPartModel,
    PipelineChunkWorkModel,
    PipelineEmbeddingBatchModel,
    PipelineRunModel,
    PipelineStageScheduleModel,
    VideoModel,
)
from src.schemas.messages import (
    EmbedBatchMessage,
    EnrichChunkMessage,
    MessageType,
    NormalizeVideoMessage,
    StageMessage,
    TranscribePartMessage,
)
from src.services.pipeline_work_scheduler import (
    DispatchCandidate,
    DispatchableStage,
    PipelineDispatchTransaction,
    ReadyEmbeddingBatchCandidate,
    ReadyWorkCandidate,
)


class TransactionBoundPublisher(Protocol):
    async def send(
        self,
        session: AsyncSession,
        queue_name: str,
        payload: dict[str, object],
    ) -> int: ...


class SqlAlchemyPipelineDispatchUnitOfWork:
    """후보 조회부터 Queue 발행과 DB 상태 저장까지 한 transaction으로 묶는다."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: TransactionBoundPublisher,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[PipelineDispatchTransaction]:
        # 이 블록 안에서 예외가 나면 PGMQ 메시지와 DB 상태 변경이 함께 rollback된다.
        async with self._session_factory() as session:
            async with session.begin():
                yield SqlAlchemyPipelineDispatchTransaction(
                    session=session,
                    publisher=self._publisher,
                )


class SqlAlchemyPipelineDispatchTransaction:
    """Scheduler가 요청한 배정 작업을 실제 SQL과 PGMQ 호출로 수행한다."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        publisher: TransactionBoundPublisher,
    ) -> None:
        self._session = session
        self._publisher = publisher

    async def acquire_stage_lock(self, stage: DispatchableStage) -> None:
        # 같은 stage의 배정기 여러 개가 capacity를 동시에 계산하지 못하게 한다.
        # transaction이 끝나면 PostgreSQL이 advisory lock을 자동으로 해제한다.
        dialect_name = self._session.get_bind().dialect.name
        if dialect_name == "sqlite":
            return
        if dialect_name != "postgresql":
            raise RuntimeError(f"Unsupported database for advisory lock: {dialect_name}")

        await self._session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('pipeline_dispatch'), hashtext(:stage))"
            ),
            {"stage": stage},
        )

    async def count_in_flight(self, stage: DispatchableStage) -> int:
        # Queue에 발행됐거나 이미 실행 중인 작업은 모두 capacity를 사용한다.
        if stage == "NORMALIZE_VIDEO":
            statement = select(func.count()).select_from(PipelineRunModel).where(
                PipelineRunModel.normalization_status.in_(("DISPATCHED", "RUNNING"))
            )
        elif stage == "TRANSCRIBE_PART":
            statement = select(func.count()).select_from(PipelineAudioPartModel).where(
                PipelineAudioPartModel.status.in_(("DISPATCHED", "RUNNING"))
            )
        elif stage == "ENRICH_CHUNK":
            statement = select(func.count()).select_from(PipelineChunkWorkModel).where(
                PipelineChunkWorkModel.enrichment_status.in_(
                    ("DISPATCHED", "RUNNING")
                )
            )
        elif stage == "EMBED_BATCH":
            statement = select(func.count()).select_from(
                PipelineEmbeddingBatchModel
            ).where(
                PipelineEmbeddingBatchModel.status.in_(("DISPATCHED", "RUNNING"))
            )
        else:
            raise ValueError(f"Unsupported dispatch stage: {stage}")

        count = await self._session.scalar(statement)
        return int(count or 0)

    async def load_ready_candidates(
        self,
        stage: DispatchableStage,
    ) -> Sequence[DispatchCandidate]:
        # stage마다 작업을 저장하는 테이블과 READY 상태 필드가 다르다.
        if stage == "NORMALIZE_VIDEO":
            return await self._load_normalization_candidates()
        if stage == "TRANSCRIBE_PART":
            return await self._load_transcription_candidates()
        if stage == "ENRICH_CHUNK":
            return await self._load_enrichment_candidates()
        if stage == "EMBED_BATCH":
            return await self._load_embedding_batch_candidates()
        raise ValueError(f"Unsupported dispatch stage: {stage}")

    #  stage별 실제 배정 함수 선택
    async def publish_and_mark_dispatched(
        self,
        stage: DispatchableStage,
        candidate: DispatchCandidate,
        trace_id: UUID,
    ) -> bool:
        if stage == "NORMALIZE_VIDEO" and isinstance(candidate, ReadyWorkCandidate):
            return await self._dispatch_normalization(candidate, trace_id)
        if stage == "TRANSCRIBE_PART" and isinstance(candidate, ReadyWorkCandidate):
            return await self._dispatch_transcription(candidate, trace_id)
        if stage == "ENRICH_CHUNK" and isinstance(candidate, ReadyWorkCandidate):
            return await self._dispatch_enrichment(candidate, trace_id)
        if stage == "EMBED_BATCH" and isinstance(
            candidate,
            ReadyEmbeddingBatchCandidate,
        ):
            return await self._dispatch_embedding_batch(candidate, trace_id)
        raise TypeError(f"Candidate does not match stage {stage}")

    async def _load_normalization_candidates(self) -> list[ReadyWorkCandidate]:
        # normalization은 영상마다 작업이 하나라 오래 기다린 READY run부터 고른다.
        statement = (
            select(PipelineRunModel.id, PipelineRunModel.video_id)
            .join(VideoModel, VideoModel.id == PipelineRunModel.video_id)
            .where(
                PipelineRunModel.is_active.is_(True),
                PipelineRunModel.normalization_status == "READY",
                VideoModel.status != "DELETING",
            )
            .order_by(
                PipelineRunModel.normalization_ready_at,
                PipelineRunModel.video_id,
            )
        )
        rows = (await self._session.execute(statement)).all()
        return [
            ReadyWorkCandidate(
                work_id=run_id,
                video_id=video_id,
                pipeline_run_id=run_id,
            )
            for run_id, video_id in rows
        ]

    async def _load_transcription_candidates(self) -> list[ReadyWorkCandidate]:
        return await self._load_video_work_candidates(
            stage="TRANSCRIBE_PART",
            work_id_column=PipelineAudioPartModel.audio_part_id,
            run_id_column=PipelineAudioPartModel.pipeline_run_id,
            ready_status=PipelineAudioPartModel.status == "READY",
            ready_at_column=PipelineAudioPartModel.ready_at,
        )

    async def _load_enrichment_candidates(self) -> list[ReadyWorkCandidate]:
        return await self._load_video_work_candidates(
            stage="ENRICH_CHUNK",
            work_id_column=PipelineChunkWorkModel.chunk_work_id,
            run_id_column=PipelineChunkWorkModel.pipeline_run_id,
            ready_status=PipelineChunkWorkModel.enrichment_status == "READY",
            ready_at_column=PipelineChunkWorkModel.enrichment_ready_at,
        )

    async def _load_video_work_candidates(
        self,
        *,
        stage: DispatchableStage,
        work_id_column,
        run_id_column,
        ready_status,
        ready_at_column,
    ) -> list[ReadyWorkCandidate]:
        # 이전에 배정받지 못한 영상, 마지막 배정이 오래된 영상 순으로 후보를 만든다.
        # 같은 영상 안에서는 READY 시각과 작업 id로 순서를 고정한다.
        statement = (
            select(work_id_column, run_id_column, PipelineRunModel.video_id)
            .join(PipelineRunModel, PipelineRunModel.id == run_id_column)
            .join(VideoModel, VideoModel.id == PipelineRunModel.video_id)
            .outerjoin(
                PipelineStageScheduleModel,
                (PipelineStageScheduleModel.pipeline_run_id == PipelineRunModel.id)
                & (PipelineStageScheduleModel.stage == stage),
            )
            .where(
                ready_status,
                PipelineRunModel.is_active.is_(True),
                VideoModel.status != "DELETING",
            )
            .order_by(
                case(
                    (PipelineStageScheduleModel.last_dispatched_at.is_(None), 0),
                    else_=1,
                ),
                PipelineStageScheduleModel.last_dispatched_at,
                ready_at_column,
                PipelineRunModel.video_id,
                work_id_column,
            )
        )
        rows = (await self._session.execute(statement)).all()
        return [
            ReadyWorkCandidate(
                work_id=work_id,
                video_id=video_id,
                pipeline_run_id=run_id,
            )
            for work_id, run_id, video_id in rows
        ]

    async def _load_embedding_batch_candidates(
        self,
    ) -> list[ReadyEmbeddingBatchCandidate]:
        # embedding은 여러 영상의 chunk가 묶인 batch 자체가 배정 단위다.
        statement = (
            select(PipelineEmbeddingBatchModel.batch_id)
            .where(PipelineEmbeddingBatchModel.status == "READY")
            .order_by(
                PipelineEmbeddingBatchModel.ready_at,
                PipelineEmbeddingBatchModel.batch_id,
            )
        )
        batch_ids = await self._session.scalars(statement)
        return [
            ReadyEmbeddingBatchCandidate(batch_id=batch_id)
            for batch_id in batch_ids
        ]

    async def _dispatch_normalization(
        self,
        candidate: ReadyWorkCandidate,
        trace_id: UUID,
    ) -> bool:
        # 후보 조회 뒤 삭제나 run 교체가 발생했을 수 있어 잠금 아래 다시 확인한다.
        run = await self._load_dispatchable_run(candidate)
        if run is None or run.normalization_status != "READY":
            return False

        attempt = run.normalization_attempt_count + 1
        message = NormalizeVideoMessage(
            message_type=MessageType.NORMALIZE_VIDEO,
            payload_version="v1",
            trace_id=trace_id,
            attempt=attempt,
            pipeline_run_id=run.id,
            video_id=run.video_id,
            pipeline_version=run.pipeline_version,
            issued_at=datetime.now(UTC),
        )
        # Queue 발행과 아래 DISPATCHED 저장은 begin()이 연 같은 transaction에 있다.
        message_id = await self._publish("NORMALIZE_VIDEO", message)
        run.normalization_status = "DISPATCHED"
        run.normalization_attempt_count = attempt
        run.normalization_message_id = message_id
        run.normalization_dispatched_at = func.now()
        await self._touch_schedule(run.id, "NORMALIZE_VIDEO")
        return True

    async def _dispatch_transcription(
        self,
        candidate: ReadyWorkCandidate,
        trace_id: UUID,
    ) -> bool:
        # 영상과 run을 확인한 뒤 정확한 audio part를 잠그고 READY를 재검사한다.
        run = await self._load_dispatchable_run(candidate)
        if run is None:
            return False
        part = await self._session.get(
            PipelineAudioPartModel,
            candidate.work_id,
            with_for_update=True,
        )
        if part is None or part.status != "READY":
            return False

        attempt = part.attempt_count + 1
        message = TranscribePartMessage(
            message_type=MessageType.TRANSCRIBE_PART,
            payload_version="v1",
            trace_id=trace_id,
            attempt=attempt,
            pipeline_run_id=run.id,
            video_id=run.video_id,
            audio_part_id=part.audio_part_id,
            part_index=part.part_index,
            stt_model_version=part.stt_model_version,
            issued_at=datetime.now(UTC),
        )
        # PGMQ가 반환한 id를 저장해야 consumer가 오래된 메시지를 구별할 수 있다.
        message_id = await self._publish("TRANSCRIBE_PART", message)
        part.status = "DISPATCHED"
        part.attempt_count = attempt
        part.message_id = message_id
        part.dispatched_at = func.now()
        await self._touch_schedule(run.id, "TRANSCRIBE_PART")
        return True

    async def _dispatch_enrichment(
        self,
        candidate: ReadyWorkCandidate,
        trace_id: UUID,
    ) -> bool:
        # transcription과 같은 원리로 chunk를 잠그고 enrichment READY를 재검사한다.
        run = await self._load_dispatchable_run(candidate)
        if run is None:
            return False
        chunk = await self._session.get(
            PipelineChunkWorkModel,
            candidate.work_id,
            with_for_update=True,
        )
        if chunk is None or chunk.enrichment_status != "READY":
            return False

        attempt = chunk.enrichment_attempt_count + 1
        message = EnrichChunkMessage(
            message_type=MessageType.ENRICH_CHUNK,
            payload_version="v1",
            trace_id=trace_id,
            attempt=attempt,
            pipeline_run_id=run.id,
            video_id=run.video_id,
            chunk_work_id=chunk.chunk_work_id,
            chunk_index=chunk.chunk_index,
            chunking_version=chunk.chunking_version,
            stt_model_version=chunk.stt_model_version,
            issued_at=datetime.now(UTC),
        )
        message_id = await self._publish("ENRICH_CHUNK", message)
        chunk.enrichment_status = "DISPATCHED"
        chunk.enrichment_attempt_count = attempt
        chunk.enrichment_message_id = message_id
        chunk.enrichment_dispatched_at = func.now()
        await self._touch_schedule(run.id, "ENRICH_CHUNK")
        return True

    async def _dispatch_embedding_batch(
        self,
        candidate: ReadyEmbeddingBatchCandidate,
        trace_id: UUID,
    ) -> bool:
        # batch와 소속 chunk가 모두 실행 가능한 경우에만 함께 DISPATCHED로 바꾼다.
        batch = await self._session.get(
            PipelineEmbeddingBatchModel,
            candidate.batch_id,
            with_for_update=True,
        )
        if batch is None or batch.status != "READY":
            return False
        if not await self._embedding_batch_is_dispatchable(batch):
            return False

        attempt = batch.attempt_count + 1
        message = EmbedBatchMessage(
            message_type=MessageType.EMBED_BATCH,
            payload_version="v1",
            trace_id=trace_id,
            attempt=attempt,
            batch_id=batch.batch_id,
            embedding_model_version=batch.embedding_model_version,
            index_name=batch.index_name,
            issued_at=datetime.now(UTC),
        )
        message_id = await self._publish("EMBED_BATCH", message)
        batch.status = "DISPATCHED"
        batch.attempt_count = attempt
        batch.message_id = message_id
        batch.dispatched_at = func.now()
        await self._session.execute(
            update(PipelineChunkWorkModel)
            .where(
                PipelineChunkWorkModel.embedding_batch_id == batch.batch_id,
                PipelineChunkWorkModel.embedding_status == "READY",
            )
            .values(
                embedding_status="DISPATCHED",
                embedding_attempt_count=(
                    PipelineChunkWorkModel.embedding_attempt_count + 1
                ),
                embedding_dispatched_at=func.now(),
            )
        )
        return True

    async def _load_dispatchable_run(
        self,
        candidate: ReadyWorkCandidate,
    ) -> PipelineRunModel | None:
        # 영상 잠금을 먼저 잡아 삭제가 시작되는 순간과 Queue 발행이 엇갈리지 않게 한다.
        video_status = await self._session.scalar(
            select(VideoModel.status)
            .where(VideoModel.id == candidate.video_id)
            .with_for_update()
        )
        if video_status is None or video_status == "DELETING":
            return None

        run = await self._session.get(
            PipelineRunModel,
            candidate.pipeline_run_id,
            with_for_update=True,
        )
        if run is None or not run.is_active or run.video_id != candidate.video_id:
            return None
        return run

    async def _embedding_batch_is_dispatchable(
        self,
        batch: PipelineEmbeddingBatchModel,
    ) -> bool:
        # batch에 포함된 영상·run·chunk 가운데 하나라도 조건이 다르면 발행하지 않는다.
        statement = (
            select(
                VideoModel.id,
                VideoModel.status,
                PipelineRunModel.is_active,
                PipelineChunkWorkModel.embedding_status,
                PipelineChunkWorkModel.embedding_model_version,
                PipelineChunkWorkModel.index_name,
            )
            .select_from(PipelineChunkWorkModel)
            .join(
                PipelineRunModel,
                PipelineRunModel.id == PipelineChunkWorkModel.pipeline_run_id,
            )
            .join(VideoModel, VideoModel.id == PipelineRunModel.video_id)
            .where(PipelineChunkWorkModel.embedding_batch_id == batch.batch_id)
            .order_by(VideoModel.id)
            .with_for_update()
        )
        rows = (await self._session.execute(statement)).all()
        return bool(rows) and all(
            video_status != "DELETING"
            and is_active
            and embedding_status == "READY"
            and model_version == batch.embedding_model_version
            and index_name == batch.index_name
            for (
                _,
                video_status,
                is_active,
                embedding_status,
                model_version,
                index_name,
            ) in rows
        )

    async def _publish(
        self,
        stage: DispatchableStage,
        message: StageMessage,
    ) -> int:
        # publisher에도 현재 AsyncSession을 넘겨 pgmq.send를 같은 transaction에 넣는다.
        payload = message.model_dump(mode="json")
        return await self._publisher.send(self._session, stage, payload)

    async def _touch_schedule(
        self,
        pipeline_run_id: UUID,
        stage: DispatchableStage,
    ) -> None:
        # 다음 배정에서 오래 기다린 영상을 먼저 고를 수 있도록 마지막 배정 시각을 남긴다.
        key = {"pipeline_run_id": pipeline_run_id, "stage": stage}
        schedule = await self._session.get(PipelineStageScheduleModel, key)
        if schedule is None:
            schedule = PipelineStageScheduleModel(**key)
            self._session.add(schedule)
        schedule.last_dispatched_at = func.now()
