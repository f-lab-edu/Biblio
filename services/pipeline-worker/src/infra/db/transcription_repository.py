from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.models import PipelineAudioPartModel, PipelineRunModel, VideoModel
from src.infra.db.pipeline_dispatch_unit_of_work import (
    SqlAlchemyPipelineDispatchTransaction,
    TransactionBoundPublisher,
)
from src.schemas.messages import TranscribePartMessage
from src.services.pipeline_work_scheduler import PipelineWorkScheduler
from src.services.transcription_artifact import TranscriptionArtifact


@dataclass(frozen=True, slots=True)
class TranscriptionInput:
    audio_gcs_path: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class TranscriptionCommitDecision:
    accepted: bool
    reason: str


class TranscriptAssemblyBoundary(Protocol):
    async def advance(
        self,
        *,
        pipeline_run_id: UUID,
        trace_id: UUID,
    ) -> object: ...


class DeferredTranscriptAssemblyBoundary:
    async def advance(
        self,
        *,
        pipeline_run_id: UUID,
        trace_id: UUID,
    ) -> None:
        del pipeline_run_id, trace_id


class TranscriptionRepository:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: TransactionBoundPublisher,
        scheduler: PipelineWorkScheduler,
        stt_capacity: int,
        assembly_boundary: TranscriptAssemblyBoundary,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._scheduler = scheduler
        self._stt_capacity = stt_capacity
        self._assembly_boundary = assembly_boundary

    async def load_input(
        self,
        message: TranscribePartMessage,
        *,
        message_id: int,
    ) -> TranscriptionInput | None:
        async with self._session_factory() as session:
            video_status = await session.scalar(
                select(VideoModel.status).where(VideoModel.id == message.video_id)
            )
            run = await session.get(PipelineRunModel, message.pipeline_run_id)
            part = await session.get(PipelineAudioPartModel, message.audio_part_id)
            if (
                video_status in {None, "DELETING"}
                or run is None
                or not run.is_active
                or run.video_id != message.video_id
                or part is None
                or part.pipeline_run_id != message.pipeline_run_id
                or part.part_index != message.part_index
                or part.stt_model_version != message.stt_model_version
                or part.message_id != message_id
                or part.status != "RUNNING"
            ):
                return None
            return TranscriptionInput(
                audio_gcs_path=part.audio_gcs_path,
                start_ms=part.start_ms,
                end_ms=part.end_ms,
            )

    async def complete(
        self,
        message: TranscribePartMessage,
        *,
        message_id: int,
        result_ref: str,
        artifact: TranscriptionArtifact,
    ) -> TranscriptionCommitDecision:
        transaction: SqlAlchemyPipelineDispatchTransaction | None = None
        decision = TranscriptionCommitDecision(False, "not_committed")
        should_advance_assembly = False
        async with self._session_factory() as session:
            async with session.begin():
                video_status = await session.scalar(
                    select(VideoModel.status)
                    .where(VideoModel.id == message.video_id)
                    .with_for_update()
                )
                run = await session.get(
                    PipelineRunModel,
                    message.pipeline_run_id,
                    with_for_update=True,
                )
                part = await session.get(
                    PipelineAudioPartModel,
                    message.audio_part_id,
                    with_for_update=True,
                )
                reason = self._completion_rejection_reason(
                    message,
                    message_id=message_id,
                    video_status=video_status,
                    run=run,
                    part=part,
                )
                if reason == "already_completed" and part is not None:
                    if part.result_ref != result_ref or not artifact.matches(
                        pipeline_run_id=message.pipeline_run_id,
                        audio_part_id=part.audio_part_id,
                        part_index=part.part_index,
                        start_ms=part.start_ms,
                        end_ms=part.end_ms,
                        stt_model_version=part.stt_model_version,
                    ):
                        return TranscriptionCommitDecision(
                            False,
                            "completed_result_mismatch",
                        )
                    decision = TranscriptionCommitDecision(True, "already_completed")
                    should_advance_assembly = True
                elif reason is not None:
                    if (
                        reason == "video_deleting"
                        and part is not None
                        and part.pipeline_run_id == message.pipeline_run_id
                    ):
                        part.status = "CANCELLED"
                        part.cancelled_at = func.now()
                    return TranscriptionCommitDecision(False, reason)
                else:
                    assert run is not None and part is not None
                    if not artifact.matches(
                        pipeline_run_id=run.id,
                        audio_part_id=part.audio_part_id,
                        part_index=part.part_index,
                        start_ms=part.start_ms,
                        end_ms=part.end_ms,
                        stt_model_version=part.stt_model_version,
                    ):
                        raise ValueError("Transcription artifact identity mismatch")

                    completed_at = await session.scalar(select(func.now()))
                    part.status = "COMPLETED"
                    part.result_ref = result_ref
                    part.failure_code = None
                    part.completed_at = completed_at
                    await session.flush()
                    transaction = SqlAlchemyPipelineDispatchTransaction(
                        session=session,
                        publisher=self._publisher,
                    )
                    await self._scheduler.dispatch_in_transaction(
                        transaction,
                        "TRANSCRIBE_PART",
                        self._stt_capacity,
                        trace_id=message.trace_id,
                    )
                    decision = TranscriptionCommitDecision(True, "completed")
                    should_advance_assembly = True
        if transaction is not None:
            transaction.emit_committed_events()
        if should_advance_assembly:
            await self._assembly_boundary.advance(
                pipeline_run_id=message.pipeline_run_id,
                trace_id=message.trace_id,
            )
        return decision

    async def fail(
        self,
        message: TranscribePartMessage,
        *,
        message_id: int,
        failure_code: str,
    ) -> bool:
        transaction: SqlAlchemyPipelineDispatchTransaction | None = None
        async with self._session_factory() as session:
            async with session.begin():
                video = await session.get(VideoModel, message.video_id, with_for_update=True)
                run = await session.get(
                    PipelineRunModel,
                    message.pipeline_run_id,
                    with_for_update=True,
                )
                part = await session.get(
                    PipelineAudioPartModel,
                    message.audio_part_id,
                    with_for_update=True,
                )
                if (
                    video is None
                    or video.status == "DELETING"
                    or run is None
                    or not run.is_active
                    or run.video_id != message.video_id
                    or part is None
                    or part.pipeline_run_id != message.pipeline_run_id
                    or part.part_index != message.part_index
                    or part.stt_model_version != message.stt_model_version
                    or part.message_id != message_id
                    or part.status != "RUNNING"
                ):
                    return False
                failed_at = await session.scalar(select(func.now()))
                part.status = "FAILED"
                part.failure_code = failure_code
                part.failed_at = failed_at
                run.status = "FAILED"
                run.is_active = False
                run.failure_code = failure_code
                video.status = "FAILED"
                video.failed_stage = "TRANSCRIBE_PART"
                video.failure_code = failure_code
                video.failure_trace_id = message.trace_id
                await session.flush()
                transaction = SqlAlchemyPipelineDispatchTransaction(
                    session=session,
                    publisher=self._publisher,
                )
                await self._scheduler.dispatch_in_transaction(
                    transaction,
                    "TRANSCRIBE_PART",
                    self._stt_capacity,
                    trace_id=message.trace_id,
                )
        if transaction is not None:
            transaction.emit_committed_events()
        return True

    @staticmethod
    def _completion_rejection_reason(
        message: TranscribePartMessage,
        *,
        message_id: int,
        video_status: str | None,
        run: PipelineRunModel | None,
        part: PipelineAudioPartModel | None,
    ) -> str | None:
        if video_status is None:
            return "video_not_found"
        if video_status == "DELETING":
            return "video_deleting"
        if run is None or not run.is_active or run.video_id != message.video_id:
            return "inactive_pipeline_run"
        if (
            part is None
            or part.pipeline_run_id != message.pipeline_run_id
            or part.part_index != message.part_index
            or part.stt_model_version != message.stt_model_version
        ):
            return "work_not_found"
        if part.message_id != message_id:
            return "stale_message_id"
        if part.status == "COMPLETED" and part.result_ref is not None:
            return "already_completed"
        if part.status != "RUNNING":
            return "terminal_or_not_running"
        return None
