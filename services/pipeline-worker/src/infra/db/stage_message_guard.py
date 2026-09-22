# queue에서 꺼낸 메세지 사용 가능을 판정

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.models import (
    PipelineAudioPartModel,
    PipelineChunkWorkModel,
    PipelineEmbeddingBatchModel,
    PipelineRunModel,
    VideoModel,
)
from src.schemas.messages import (
    EmbedBatchMessage,
    EnrichChunkMessage,
    NormalizeVideoMessage,
    StageMessage,
    TranscribePartMessage,
)

# 메세지 통과기준
EXECUTABLE_STATUSES = frozenset({"DISPATCHED", "RUNNING"})


@dataclass(frozen=True, slots=True)
class StageMessageDecision:
    should_execute: bool
    reason: str
    state_changed_at: datetime | None = None


class SqlAlchemyStageMessageClaimer:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def claim_for_execution(
        self,
        message: StageMessage,
        message_id: int,
    ) -> StageMessageDecision:
        async with self._session_factory() as session:
            async with session.begin():
                if isinstance(message, NormalizeVideoMessage):
                    return await self._claim_normalization(
                        session,
                        message,
                        message_id,
                    )
                if isinstance(message, TranscribePartMessage):
                    return await self._claim_transcription(
                        session,
                        message,
                        message_id,
                    )
                if isinstance(message, EnrichChunkMessage):
                    return await self._claim_enrichment(
                        session,
                        message,
                        message_id,
                    )
                if isinstance(message, EmbedBatchMessage):
                    return await self._claim_embedding_batch(
                        session,
                        message,
                        message_id,
                    )
        raise TypeError(f"Unsupported stage message: {type(message).__name__}")

    async def _claim_normalization(
        self,
        session: AsyncSession,
        message: NormalizeVideoMessage,
        message_id: int,
    ) -> StageMessageDecision:
        video_status = await self._lock_video_status(session, message.video_id)
        if video_status is None:
            return StageMessageDecision(False, "video_not_found")
        run = await session.get(
            PipelineRunModel,
            message.pipeline_run_id,
            with_for_update=True,
        )
        if run is None or run.video_id != message.video_id:
            return StageMessageDecision(False, "work_not_found")
        if not run.is_active:
            return StageMessageDecision(False, "inactive_pipeline_run")
        if run.normalization_message_id != message_id:
            return StageMessageDecision(False, "stale_message_id")
        if run.normalization_status not in EXECUTABLE_STATUSES:
            return StageMessageDecision(False, "terminal_or_not_dispatched")
        if video_status == "DELETING":
            self._cancel_normalization(run)
            return StageMessageDecision(False, "video_deleting")
        if run.normalization_status == "DISPATCHED":
            started_at = await session.scalar(select(func.now()))
            run.normalization_status = "RUNNING"
            run.normalization_started_at = started_at
        else:
            started_at = run.normalization_started_at
        return StageMessageDecision(True, "executable", started_at)

    async def _claim_transcription(
        self,
        session: AsyncSession,
        message: TranscribePartMessage,
        message_id: int,
    ) -> StageMessageDecision:
        video_status = await self._lock_video_status(session, message.video_id)
        if video_status is None:
            return StageMessageDecision(False, "video_not_found")
        run = await self._load_active_run(session, message.pipeline_run_id)
        if run is None or run.video_id != message.video_id:
            return StageMessageDecision(False, "inactive_pipeline_run")
        part = await session.get(
            PipelineAudioPartModel,
            message.audio_part_id,
            with_for_update=True,
        )
        if part is None or part.pipeline_run_id != message.pipeline_run_id:
            return StageMessageDecision(False, "work_not_found")
        if part.message_id != message_id:
            return StageMessageDecision(False, "stale_message_id")
        if part.status not in EXECUTABLE_STATUSES:
            return StageMessageDecision(False, "terminal_or_not_dispatched")
        if video_status == "DELETING":
            self._cancel_audio_part(part)
            return StageMessageDecision(False, "video_deleting")
        if part.status == "DISPATCHED":
            started_at = await session.scalar(select(func.now()))
            part.status = "RUNNING"
            part.started_at = started_at
        else:
            started_at = part.started_at
        return StageMessageDecision(True, "executable", started_at)

    async def _claim_enrichment(
        self,
        session: AsyncSession,
        message: EnrichChunkMessage,
        message_id: int,
    ) -> StageMessageDecision:
        video_status = await self._lock_video_status(session, message.video_id)
        if video_status is None:
            return StageMessageDecision(False, "video_not_found")
        run = await self._load_active_run(session, message.pipeline_run_id)
        if run is None or run.video_id != message.video_id:
            return StageMessageDecision(False, "inactive_pipeline_run")
        chunk = await session.get(
            PipelineChunkWorkModel,
            message.chunk_work_id,
            with_for_update=True,
        )
        if chunk is None or chunk.pipeline_run_id != message.pipeline_run_id:
            return StageMessageDecision(False, "work_not_found")
        if chunk.enrichment_message_id != message_id:
            return StageMessageDecision(False, "stale_message_id")
        if chunk.enrichment_status not in EXECUTABLE_STATUSES:
            return StageMessageDecision(False, "terminal_or_not_dispatched")
        if video_status == "DELETING":
            self._cancel_enrichment(chunk)
            return StageMessageDecision(False, "video_deleting")
        if chunk.enrichment_status == "DISPATCHED":
            started_at = await session.scalar(select(func.now()))
            chunk.enrichment_status = "RUNNING"
            chunk.enrichment_started_at = started_at
        else:
            started_at = chunk.enrichment_started_at
        return StageMessageDecision(True, "executable", started_at)

    async def _claim_embedding_batch(
        self,
        session: AsyncSession,
        message: EmbedBatchMessage,
        message_id: int,
    ) -> StageMessageDecision:
        video_rows = (
            await session.execute(
                select(VideoModel.id, VideoModel.status)
                .where(
                    VideoModel.id.in_(
                        select(PipelineRunModel.video_id)
                        .join(
                            PipelineChunkWorkModel,
                            PipelineChunkWorkModel.pipeline_run_id
                            == PipelineRunModel.id,
                        )
                        .where(
                            PipelineChunkWorkModel.embedding_batch_id
                            == message.batch_id
                        )
                    )
                )
                .order_by(VideoModel.id)
                .with_for_update()
            )
        ).all()
        if not video_rows:
            return StageMessageDecision(False, "work_not_found")

        run_rows = (
            await session.execute(
                select(
                    PipelineRunModel.id,
                    PipelineRunModel.is_active,
                    PipelineRunModel.video_id,
                )
                .join(
                    PipelineChunkWorkModel,
                    PipelineChunkWorkModel.pipeline_run_id == PipelineRunModel.id,
                )
                .where(
                    PipelineChunkWorkModel.embedding_batch_id == message.batch_id
                )
                .order_by(PipelineRunModel.id)
                .with_for_update()
            )
        ).all()
        batch = await session.get(
            PipelineEmbeddingBatchModel,
            message.batch_id,
            with_for_update=True,
        )
        if batch is None:
            return StageMessageDecision(False, "work_not_found")
        if batch.message_id != message_id:
            return StageMessageDecision(False, "stale_message_id")
        if batch.status not in EXECUTABLE_STATUSES:
            return StageMessageDecision(False, "terminal_or_not_dispatched")

        executable_run_ids, excluded_run_ids = self._partition_embedding_runs(
            video_rows,
            run_rows,
        )
        await self._cancel_excluded_embedding_work(
            session,
            batch.batch_id,
            excluded_run_ids,
        )
        if not executable_run_ids:
            batch.status = "CANCELLED"
            batch.cancelled_at = func.now()
            return StageMessageDecision(False, "no_executable_batch_work")
        if batch.status == "DISPATCHED":
            started_at = await session.scalar(select(func.now()))
            batch.status = "RUNNING"
            batch.started_at = started_at
            await session.execute(
                update(PipelineChunkWorkModel)
                .where(
                    PipelineChunkWorkModel.embedding_batch_id == batch.batch_id,
                    PipelineChunkWorkModel.pipeline_run_id.in_(executable_run_ids),
                    PipelineChunkWorkModel.embedding_status == "DISPATCHED",
                )
                .values(
                    embedding_status="RUNNING",
                    embedding_started_at=started_at,
                )
            )
        else:
            started_at = batch.started_at
        return StageMessageDecision(True, "executable", started_at)

    @staticmethod
    def _partition_embedding_runs(
        video_rows,
        run_rows,
    ) -> tuple[set[UUID], set[UUID]]:
        video_statuses = dict(video_rows)
        executable_run_ids = {
            run_id
            for run_id, is_active, video_id in run_rows
            if is_active
            and video_statuses.get(video_id) not in {None, "DELETING"}
        }
        all_run_ids = {run_id for run_id, _, _ in run_rows}
        return executable_run_ids, all_run_ids - executable_run_ids

    @staticmethod
    async def _cancel_excluded_embedding_work(
        session: AsyncSession,
        batch_id: UUID,
        excluded_run_ids: set[UUID],
    ) -> None:
        if not excluded_run_ids:
            return
        await session.execute(
            update(PipelineChunkWorkModel)
            .where(
                PipelineChunkWorkModel.embedding_batch_id == batch_id,
                PipelineChunkWorkModel.pipeline_run_id.in_(excluded_run_ids),
                PipelineChunkWorkModel.embedding_status.in_(
                    ("READY", "DISPATCHED", "RUNNING")
                ),
            )
            .values(
                embedding_status="CANCELLED",
                embedding_cancelled_at=func.now(),
            )
        )

    @staticmethod
    async def _load_active_run(
        session: AsyncSession,
        pipeline_run_id: UUID,
    ) -> PipelineRunModel | None:
        run = await session.get(
            PipelineRunModel,
            pipeline_run_id,
            with_for_update=True,
        )
        if run is None or not run.is_active:
            return None
        return run

    @staticmethod
    async def _lock_video_status(
        session: AsyncSession,
        video_id: UUID,
    ) -> str | None:
        return await session.scalar(
            select(VideoModel.status)
            .where(VideoModel.id == video_id)
            .with_for_update()
        )

    @staticmethod
    def _cancel_normalization(run: PipelineRunModel) -> None:
        if run.normalization_status == "DISPATCHED":
            run.normalization_status = "CANCELLED"
            run.normalization_cancelled_at = func.now()

    @staticmethod
    def _cancel_audio_part(part: PipelineAudioPartModel) -> None:
        if part.status == "DISPATCHED":
            part.status = "CANCELLED"
            part.cancelled_at = func.now()

    @staticmethod
    def _cancel_enrichment(chunk: PipelineChunkWorkModel) -> None:
        if chunk.enrichment_status == "DISPATCHED":
            chunk.enrichment_status = "CANCELLED"
            chunk.enrichment_cancelled_at = func.now()
