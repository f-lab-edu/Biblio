from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.models import (
    PipelineAudioPartModel,
    PipelineChunkWorkModel,
    PipelineEmbeddingBatchModel,
    PipelineRunModel,
    VideoModel,
)
from src.infra.db.pipeline_work_records import (
    PipelineRunRecord,
    RunStatus,
    WorkStatus,
    WorkTimestamps,
)


class PipelineVideoNotFoundError(LookupError):
    pass


class PipelineVideoDeletingError(RuntimeError):
    pass


class PipelineVideoNotDeletingError(RuntimeError):
    pass


class PipelineWorkRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_pipeline_run(
        self,
        video_id: UUID | str,
        pipeline_version: str,
    ) -> PipelineRunRecord:
        normalized_video_id = self._normalize_uuid(video_id)

        async with self._session_factory() as session:
            async with session.begin():
                video_status = await session.scalar(
                    select(VideoModel.status)
                    .where(VideoModel.id == normalized_video_id)
                    .with_for_update()
                )
                if video_status is None:
                    raise PipelineVideoNotFoundError(str(normalized_video_id))
                if video_status == "DELETING":
                    raise PipelineVideoDeletingError(str(normalized_video_id))

                await session.execute(
                    update(PipelineRunModel)
                    .where(
                        PipelineRunModel.video_id == normalized_video_id,
                        PipelineRunModel.is_active.is_(True) # 이 video에 속한 run 중 현재 활성 상태인 run만 찾아 SUPERSEDED로 종료
                    )
                    .values(
                        status="SUPERSEDED",
                        is_active=False,
                        updated_at=func.now(),
                    )
                )

                model = PipelineRunModel(
                    id=uuid4(),
                    video_id=normalized_video_id,
                    pipeline_version=pipeline_version,
                    normalization_ready_at=func.now(),
                )
                session.add(model)
                await session.flush()
                await session.refresh(model)

                return self._to_run_record(model)

    async def start_pipeline_run(
        self,
        video_id: UUID | str,
        pipeline_version: str,
    ) -> PipelineRunRecord | None:
        """Create the first work-unit run, or reuse the active one on redelivery."""
        normalized_video_id = self._normalize_uuid(video_id)

        async with self._session_factory() as session:
            async with session.begin():
                video = await session.scalar(
                    select(VideoModel)
                    .where(VideoModel.id == normalized_video_id)
                    .with_for_update()
                )
                if video is None:
                    raise PipelineVideoNotFoundError(str(normalized_video_id))
                if video.status == "DELETING":
                    raise PipelineVideoDeletingError(str(normalized_video_id))

                active_run = await session.scalar(
                    select(PipelineRunModel).where(
                        PipelineRunModel.video_id == normalized_video_id,
                        PipelineRunModel.is_active.is_(True),
                    )
                )
                if active_run is not None:
                    return self._to_run_record(active_run)
                if video.status in {"READY", "FAILED"}:
                    return None

                video.status = "PROCESSING"
                video.failed_stage = None
                video.failure_code = None
                video.failure_trace_id = None
                model = PipelineRunModel(
                    id=uuid4(),
                    video_id=normalized_video_id,
                    pipeline_version=pipeline_version,
                    normalization_ready_at=func.now(),
                )
                session.add(model)
                await session.flush()
                await session.refresh(model)
                return self._to_run_record(model)

    async def get_active_pipeline_run(
        self,
        video_id: UUID | str,
    ) -> PipelineRunRecord | None:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            model = await session.scalar(
                select(PipelineRunModel).where(
                    PipelineRunModel.video_id == normalized_video_id,
                    PipelineRunModel.is_active.is_(True),
                )
            )
            return self._to_run_record(model) if model is not None else None

    async def count_running_work(self, video_id: UUID | str) -> int:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            run_ids = select(PipelineRunModel.id).where(
                PipelineRunModel.video_id == normalized_video_id
            )
            counts = (
                await self._count_running_normalization(session, normalized_video_id),
                await self._count_running_audio_parts(session, run_ids),
                await self._count_running_enrichment(session, run_ids),
                await self._count_running_embedding_batches(session, run_ids),
            )
            return sum(counts)

    async def is_deletion_waiting(self, video_id: UUID | str) -> bool:
        return await self.count_running_work(video_id) > 0

    async def cancel_pending_work_for_deleting_video(
        self,
        video_id: UUID | str,
    ) -> int:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            async with session.begin():
                video_status = await session.scalar(
                    select(VideoModel.status)
                    .where(VideoModel.id == normalized_video_id)
                    .with_for_update()
                )
                if video_status is None:
                    raise PipelineVideoNotFoundError(str(normalized_video_id))
                if video_status != "DELETING":
                    raise PipelineVideoNotDeletingError(str(normalized_video_id))

                run_ids = select(PipelineRunModel.id).where(
                    PipelineRunModel.video_id == normalized_video_id
                )
                cancelled_count = await self._cancel_pending_normalization(
                    session,
                    normalized_video_id,
                )
                cancelled_count += await self._cancel_pending_audio_parts(
                    session,
                    run_ids,
                )
                cancelled_count += await self._cancel_pending_chunk_work(
                    session,
                    run_ids,
                )
                return cancelled_count

    async def finalize_deleting_runs(self, video_id: UUID | str) -> None:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(PipelineRunModel)
                    .where(
                        PipelineRunModel.video_id == normalized_video_id,
                        PipelineRunModel.status == "RUNNING",
                    )
                    .values(
                        status="CANCELLED",
                        is_active=False,
                        updated_at=func.now(),
                    )
                )

    async def recover_stale_dispatched_work(
        self,
        stage: Literal[
            "NORMALIZE_VIDEO",
            "TRANSCRIBE_PART",
            "ENRICH_CHUNK",
            "EMBED_BATCH",
        ],
        *,
        visibility_timeout_sec: int,
    ) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=visibility_timeout_sec)
        async with self._session_factory() as session:
            async with session.begin():
                if stage == "NORMALIZE_VIDEO":
                    return await self._recover_normalization(session, cutoff)
                if stage == "TRANSCRIBE_PART":
                    return await self._recover_audio_parts(session, cutoff)
                if stage == "ENRICH_CHUNK":
                    return await self._recover_enrichment(session, cutoff)
                return await self._recover_embedding_batches(session, cutoff)

    async def _recover_normalization(
        self,
        session: AsyncSession,
        cutoff: datetime,
    ) -> int:
        result = await session.execute(
            update(PipelineRunModel)
            .where(
                PipelineRunModel.is_active.is_(True),
                PipelineRunModel.normalization_status == "DISPATCHED",
                PipelineRunModel.normalization_dispatched_at <= cutoff,
                PipelineRunModel.video_id.in_(
                    select(VideoModel.id).where(VideoModel.status != "DELETING")
                ),
            )
            .values(
                normalization_status="READY",
                normalization_message_id=None,
                normalization_ready_at=func.now(),
                updated_at=func.now(),
            )
        )
        return int(result.rowcount or 0)

    async def _recover_audio_parts(
        self,
        session: AsyncSession,
        cutoff: datetime,
    ) -> int:
        result = await session.execute(
            update(PipelineAudioPartModel)
            .where(
                PipelineAudioPartModel.status == "DISPATCHED",
                PipelineAudioPartModel.dispatched_at <= cutoff,
                PipelineAudioPartModel.pipeline_run_id.in_(self._recoverable_run_ids()),
            )
            .values(
                status="READY",
                message_id=None,
                ready_at=func.now(),
                updated_at=func.now(),
            )
        )
        return int(result.rowcount or 0)

    async def _recover_enrichment(
        self,
        session: AsyncSession,
        cutoff: datetime,
    ) -> int:
        result = await session.execute(
            update(PipelineChunkWorkModel)
            .where(
                PipelineChunkWorkModel.enrichment_status == "DISPATCHED",
                PipelineChunkWorkModel.enrichment_dispatched_at <= cutoff,
                PipelineChunkWorkModel.pipeline_run_id.in_(self._recoverable_run_ids()),
            )
            .values(
                enrichment_status="READY",
                enrichment_message_id=None,
                enrichment_ready_at=func.now(),
                updated_at=func.now(),
            )
        )
        return int(result.rowcount or 0)

    @staticmethod
    def _recoverable_run_ids():
        return select(PipelineRunModel.id).where(
            PipelineRunModel.is_active.is_(True),
            PipelineRunModel.video_id.in_(
                select(VideoModel.id).where(VideoModel.status != "DELETING")
            ),
        )

    async def _recover_embedding_batches(
        self,
        session: AsyncSession,
        cutoff: datetime,
    ) -> int:
        batch_ids = select(PipelineEmbeddingBatchModel.batch_id).where(
            PipelineEmbeddingBatchModel.status == "DISPATCHED",
            PipelineEmbeddingBatchModel.dispatched_at <= cutoff,
        )
        await session.execute(
            update(PipelineChunkWorkModel)
            .where(
                PipelineChunkWorkModel.embedding_batch_id.in_(batch_ids),
                PipelineChunkWorkModel.embedding_status == "DISPATCHED",
                PipelineChunkWorkModel.pipeline_run_id.in_(self._recoverable_run_ids()),
            )
            .values(
                embedding_status="READY",
                embedding_ready_at=func.now(),
                updated_at=func.now(),
            )
        )
        result = await session.execute(
            update(PipelineEmbeddingBatchModel)
            .where(PipelineEmbeddingBatchModel.batch_id.in_(batch_ids))
            .values(
                status="READY",
                message_id=None,
                ready_at=func.now(),
                updated_at=func.now(),
            )
        )
        return int(result.rowcount or 0)

    @staticmethod
    async def _count_running_normalization(
        session: AsyncSession,
        video_id: UUID,
    ) -> int:
        count = await session.scalar(
            select(func.count())
            .select_from(PipelineRunModel)
            .where(
                PipelineRunModel.video_id == video_id,
                PipelineRunModel.normalization_status == "RUNNING",
            )
        )
        return int(count or 0)

    @staticmethod
    async def _count_running_audio_parts(
        session: AsyncSession,
        run_ids,
    ) -> int:
        count = await session.scalar(
            select(func.count())
            .select_from(PipelineAudioPartModel)
            .where(
                PipelineAudioPartModel.pipeline_run_id.in_(run_ids),
                PipelineAudioPartModel.status == "RUNNING",
            )
        )
        return int(count or 0)

    @staticmethod
    async def _count_running_enrichment(
        session: AsyncSession,
        run_ids,
    ) -> int:
        count = await session.scalar(
            select(func.count())
            .select_from(PipelineChunkWorkModel)
            .where(
                PipelineChunkWorkModel.pipeline_run_id.in_(run_ids),
                PipelineChunkWorkModel.enrichment_status == "RUNNING",
            )
        )
        return int(count or 0)

    @staticmethod
    async def _count_running_embedding_batches(
        session: AsyncSession,
        run_ids,
    ) -> int:
        count = await session.scalar(
            select(func.count(func.distinct(PipelineChunkWorkModel.embedding_batch_id)))
            .where(
                PipelineChunkWorkModel.pipeline_run_id.in_(run_ids),
                PipelineChunkWorkModel.embedding_status == "RUNNING",
                PipelineChunkWorkModel.embedding_batch_id.is_not(None),
            )
        )
        return int(count or 0)

    @staticmethod
    async def _cancel_pending_normalization(
        session: AsyncSession,
        video_id: UUID,
    ) -> int:
        result = await session.execute(
            update(PipelineRunModel)
            .where(
                PipelineRunModel.video_id == video_id,
                PipelineRunModel.normalization_status.in_(("READY", "DISPATCHED")),
            )
            .values(
                normalization_status="CANCELLED",
                normalization_cancelled_at=func.now(),
                updated_at=func.now(),
            )
        )
        return int(result.rowcount or 0)

    @staticmethod
    async def _cancel_pending_audio_parts(
        session: AsyncSession,
        run_ids,
    ) -> int:
        result = await session.execute(
            update(PipelineAudioPartModel)
            .where(
                PipelineAudioPartModel.pipeline_run_id.in_(run_ids),
                PipelineAudioPartModel.status.in_(("READY", "DISPATCHED")),
            )
            .values(
                status="CANCELLED",
                cancelled_at=func.now(),
                updated_at=func.now(),
            )
        )
        return int(result.rowcount or 0)

    @staticmethod
    async def _cancel_pending_chunk_work(
        session: AsyncSession,
        run_ids,
    ) -> int:
        enrichment_result = await session.execute(
            update(PipelineChunkWorkModel)
            .where(
                PipelineChunkWorkModel.pipeline_run_id.in_(run_ids),
                PipelineChunkWorkModel.enrichment_status.in_(("READY", "DISPATCHED")),
            )
            .values(
                enrichment_status="CANCELLED",
                enrichment_cancelled_at=func.now(),
                updated_at=func.now(),
            )
        )
        embedding_result = await session.execute(
            update(PipelineChunkWorkModel)
            .where(
                PipelineChunkWorkModel.pipeline_run_id.in_(run_ids),
                PipelineChunkWorkModel.embedding_status.in_(("READY", "DISPATCHED")),
            )
            .values(
                embedding_status="CANCELLED",
                embedding_cancelled_at=func.now(),
                updated_at=func.now(),
            )
        )
        return int(enrichment_result.rowcount or 0) + int(
            embedding_result.rowcount or 0
        )

    @staticmethod
    def _to_run_record(model: PipelineRunModel) -> PipelineRunRecord:
        return PipelineRunRecord(
            id=model.id,
            video_id=model.video_id,
            pipeline_version=model.pipeline_version,
            status=cast(RunStatus, model.status),
            is_active=model.is_active,
            normalization_status=cast(WorkStatus, model.normalization_status),
            normalization_attempt_count=model.normalization_attempt_count,
            normalization_message_id=model.normalization_message_id,
            normalization_completed=model.normalization_completed,
            transcript_completed=model.transcript_completed,
            assembly_completed=model.assembly_completed,
            total_part_count=model.total_part_count,
            next_part_index=model.next_part_index,
            next_chunk_index=model.next_chunk_index,
            pending_words=model.pending_words,
            chunk_buffer=model.chunk_buffer,
            failure_code=model.failure_code,
            normalization_timestamps=WorkTimestamps(
                ready_at=model.normalization_ready_at,
                dispatched_at=model.normalization_dispatched_at,
                started_at=model.normalization_started_at,
                completed_at=model.normalization_completed_at,
                failed_at=model.normalization_failed_at,
                cancelled_at=model.normalization_cancelled_at,
            ),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _normalize_uuid(value: UUID | str) -> UUID:
        if isinstance(value, UUID):
            return value
        return UUID(str(value))
