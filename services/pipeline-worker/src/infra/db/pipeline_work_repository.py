from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.models import PipelineRunModel, VideoModel
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
