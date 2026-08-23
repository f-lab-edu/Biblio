from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.models import (
    PipelineAudioPartModel,
    PipelineFrameCandidateModel,
    PipelineRunModel,
    VideoModel,
)
from src.infra.db.pipeline_dispatch_unit_of_work import (
    SqlAlchemyPipelineDispatchTransaction,
    TransactionBoundPublisher,
)
from src.services.normalization_service import (
    FrameCandidate,
    NormalizationPart,
    NormalizationResumeState,
    PersistedNormalizationPart,
)
from src.services.pipeline_work_scheduler import PipelineWorkScheduler

#  normalization 결과를 DB에 기록
class NormalizationRepository:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: TransactionBoundPublisher,
        scheduler: PipelineWorkScheduler,
        stt_capacity: int,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._scheduler = scheduler
        self._stt_capacity = stt_capacity

    async def get_resume_state(
        self,
        *,
        video_id: UUID,
        pipeline_run_id: UUID,
    ) -> NormalizationResumeState | None:
        async with self._session_factory() as session:
            async with session.begin():
                run = await self._lock_active_run(
                    session,
                    video_id=video_id,
                    pipeline_run_id=pipeline_run_id,
                )
                if run is None:
                    return None
                if run.normalization_status == "COMPLETED":
                    return None
                source_path = await session.scalar(
                    select(VideoModel.storage_path).where(VideoModel.id == video_id)
                )
                if source_path is None:
                    raise RuntimeError("Active normalization has no source storage path")
                if (
                    run.source_storage_path is not None
                    and run.source_storage_path != source_path
                ):
                    raise RuntimeError(
                        "Normalization source storage path changed during retry"
                    )
                parts = tuple(
                    PersistedNormalizationPart(
                        part_index=model.part_index,
                        start_ms=model.start_ms,
                        end_ms=model.end_ms,
                        storage_path=model.audio_gcs_path,
                        stt_model_version=model.stt_model_version,
                        status=model.status,
                    )
                    for model in await session.scalars(
                        select(PipelineAudioPartModel).where(
                            PipelineAudioPartModel.pipeline_run_id
                            == pipeline_run_id
                        )
                    )
                )
                frames = tuple(
                    FrameCandidate(
                        frame_index=model.frame_index,
                        timestamp_ms=model.timestamp_ms,
                        storage_path=model.frame_gcs_path,
                    )
                    for model in await session.scalars(
                        select(PipelineFrameCandidateModel).where(
                            PipelineFrameCandidateModel.pipeline_run_id
                            == pipeline_run_id
                        )
                    )
                )
                return NormalizationResumeState(
                    source_path=source_path,
                    source_generation=run.source_generation,
                    parts=parts,
                    frames=frames,
                )

    async def bind_source_identity(
        self,
        *,
        video_id: UUID,
        pipeline_run_id: UUID,
        storage_path: str,
        generation: str,
    ) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                run = await self._lock_active_run(
                    session,
                    video_id=video_id,
                    pipeline_run_id=pipeline_run_id,
                )
                if run is None:
                    return False
                if run.source_storage_path is None:
                    run.source_storage_path = storage_path
                elif run.source_storage_path != storage_path:
                    raise RuntimeError(
                        "Normalization source storage path changed during retry"
                    )
                if run.source_generation is None:
                    run.source_generation = generation
                elif run.source_generation != generation:
                    raise RuntimeError(
                        "Normalization source generation changed during retry"
                    )
                return True

    async def should_discard_artifacts(
        self,
        *,
        video_id: UUID,
        pipeline_run_id: UUID,
    ) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                run = await self._lock_active_run(
                    session,
                    video_id=video_id,
                    pipeline_run_id=pipeline_run_id,
                )
                return run is None

    async def complete_part_and_dispatch(
        self,
        *,
        video_id: UUID,
        pipeline_run_id: UUID,
        part: NormalizationPart,
        stt_model_version: str,
        trace_id: UUID,
    ) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                run = await self._lock_active_run(
                    session,
                    video_id=video_id,
                    pipeline_run_id=pipeline_run_id,
                )
                if run is None:
                    return False

                existing = await session.scalar(
                    select(PipelineAudioPartModel)
                    .where(
                        PipelineAudioPartModel.pipeline_run_id
                        == pipeline_run_id,
                        PipelineAudioPartModel.part_index
                        == part.part_index
                    )
                    .with_for_update()
                )

                if existing is None:
                    if run.normalization_status == "COMPLETED":
                        return False

                    session.add(
                        PipelineAudioPartModel(
                            audio_part_id=uuid4(),
                            pipeline_run_id=pipeline_run_id,
                            part_index=part.part_index,
                            start_ms=part.start_ms,
                            end_ms=part.end_ms,
                            audio_gcs_path=part.storage_path,
                            stt_model_version=stt_model_version,
                            status="READY",
                            ready_at=func.now(),
                        )
                    )
                else:
                    identity_changed = (
                        existing.start_ms != part.start_ms
                        or existing.end_ms != part.end_ms
                        or existing.audio_gcs_path != part.storage_path
                        or existing.stt_model_version != stt_model_version
                    )
                    if identity_changed:
                        raise RuntimeError(
                            "Audio part identity changed during retry"
                        )
                    if existing.status in {"FAILED", "CANCELLED"}:
                        return False

                await session.flush()

                transaction = SqlAlchemyPipelineDispatchTransaction(
                    session=session,
                    publisher=self._publisher,
                )
                await self._scheduler.dispatch_in_transaction(
                    transaction,
                    "TRANSCRIBE_PART",
                    self._stt_capacity,
                    trace_id=trace_id,
                )
                return True

    # 후보 JPEG 업로드가 끝난 뒤 metadata를 저장
    async def save_frame_candidate(
        self,
        *,
        video_id: UUID,
        pipeline_run_id: UUID,
        frame_index: int,
        timestamp_ms: int,
        frame_gcs_path: str,
    ) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                run = await self._lock_active_run(
                    session,
                    video_id=video_id,
                    pipeline_run_id=pipeline_run_id,
                )
                if run is None:
                    return False
                existing = await session.scalar(
                    select(PipelineFrameCandidateModel)
                    .where(
                        PipelineFrameCandidateModel.pipeline_run_id
                        == pipeline_run_id,
                        or_(
                            PipelineFrameCandidateModel.frame_index
                            == frame_index,
                            PipelineFrameCandidateModel.timestamp_ms
                            == timestamp_ms,
                        ),
                    )
                    .with_for_update()
                )
                if existing is None:
                    session.add(
                        PipelineFrameCandidateModel(
                            frame_candidate_id=uuid4(),
                            pipeline_run_id=pipeline_run_id,
                            frame_index=frame_index,
                            timestamp_ms=timestamp_ms,
                            frame_gcs_path=frame_gcs_path,
                        )
                    )
                elif (
                    existing.frame_index != frame_index
                    or existing.frame_gcs_path != frame_gcs_path
                ):
                    raise RuntimeError("Frame candidate identity changed during retry")
                return True

    async def complete_normalization(
        self,
        *,
        video_id: UUID,
        pipeline_run_id: UUID,
        total_part_count: int,
        total_frame_count: int,
    ) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                run = await self._lock_active_run(
                    session,
                    video_id=video_id,
                    pipeline_run_id=pipeline_run_id,
                )
                if run is None:
                    return False
                # db에 저장된 part 개수 확인
                part_count = await session.scalar(
                    select(func.count())
                    .select_from(PipelineAudioPartModel)
                    .where(
                        PipelineAudioPartModel.pipeline_run_id == pipeline_run_id
                    )
                )
                if int(part_count or 0) != total_part_count:
                    raise RuntimeError("Cannot complete normalization with missing parts")
                frame_count = await session.scalar(
                    select(func.count())
                    .select_from(PipelineFrameCandidateModel)
                    .where(
                        PipelineFrameCandidateModel.pipeline_run_id
                        == pipeline_run_id
                    )
                )
                if int(frame_count or 0) != total_frame_count:
                    raise RuntimeError(
                        "Cannot complete normalization with missing frame candidates"
                    )
                run.total_part_count = total_part_count
                run.normalization_status = "COMPLETED"
                run.normalization_completed = True
                run.normalization_completed_at = func.now()
                return True

    # 결과를 저장하기 직전에 영상과 run을 다시 확인
    @staticmethod
    async def _lock_active_run(
        session: AsyncSession,
        *,
        video_id: UUID,
        pipeline_run_id: UUID,
    ) -> PipelineRunModel | None:
        video_status = await session.scalar(
            select(VideoModel.status)
            .where(VideoModel.id == video_id)
            .with_for_update()
        )
        if video_status is None or video_status == "DELETING":
            return None
        run = await session.get(
            PipelineRunModel,
            pipeline_run_id,
            with_for_update=True,
        )
        if (
            run is None
            or not run.is_active
            or run.video_id != video_id
            or run.normalization_status not in {"RUNNING", "COMPLETED"}
        ):
            return None
        return run
