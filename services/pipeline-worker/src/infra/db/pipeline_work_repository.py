from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.models import (
    PipelineAudioPartModel,
    PipelineChunkWorkModel,
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
                return cancelled_count

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
