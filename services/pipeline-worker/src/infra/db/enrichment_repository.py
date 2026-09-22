from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.ai.vision_adapter import VisionResult
from src.infra.db.models import (
    AssetModel,
    ChunkModel,
    PipelineChunkWorkModel,
    PipelineRunModel,
    VideoModel,
)
from src.infra.db.pipeline_dispatch_unit_of_work import (
    SqlAlchemyPipelineDispatchTransaction,
    TransactionBoundPublisher,
)
from src.schemas.messages import EnrichChunkMessage
from src.services.enrichment_service import (
    EnrichmentCommitDecision,
    EnrichmentInput,
)
from src.services.pipeline_work_scheduler import PipelineWorkScheduler


@dataclass(frozen=True, slots=True)
class _LockedEnrichmentState:
    video: VideoModel | None
    run: PipelineRunModel | None
    work: PipelineChunkWorkModel | None


class SqlAlchemyEnrichmentRepository:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: TransactionBoundPublisher,
        scheduler: PipelineWorkScheduler,
        enrichment_capacity: int,
        embedding_capacity: int,
        embedding_batch_size: int = 16,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._scheduler = scheduler
        self._enrichment_capacity = enrichment_capacity
        self._embedding_capacity = embedding_capacity
        self._embedding_batch_size = embedding_batch_size

    async def load_input(
        self,
        message: EnrichChunkMessage,
        *,
        message_id: int,
    ) -> EnrichmentInput | None:
        async with self._session_factory() as session:
            state = await self._load_state(session, message, lock=False)
            if self._rejection_reason(message, message_id, state) is not None:
                return None
            assert state.work is not None and state.work.frame_ref is not None
            return EnrichmentInput(
                text=state.work.text,
                start_ms=state.work.start_ms,
                end_ms=state.work.end_ms,
                frame_ref=state.work.frame_ref,
            )

    async def complete(
        self,
        message: EnrichChunkMessage,
        *,
        message_id: int,
        keyframe_ref: str,
        vision_result: VisionResult,
        enriched_text: str,
    ) -> EnrichmentCommitDecision:
        transactions: list[SqlAlchemyPipelineDispatchTransaction] = []
        async with self._session_factory() as session:
            async with session.begin():
                state = await self._load_state(session, message, lock=True)
                reason = self._rejection_reason(message, message_id, state)
                if reason == "already_completed":
                    return self._completed_decision(message, state)
                if reason is not None:
                    self._cancel_if_deleting(reason, state.work)
                    return EnrichmentCommitDecision(False, reason)

                assert state.work is not None
                self._persist_result(
                    session,
                    message,
                    work=state.work,
                    keyframe_ref=keyframe_ref,
                    vision_result=vision_result,
                    enriched_text=enriched_text,
                )
                completed_at = await session.scalar(select(func.now()))
                state.work.enrichment_status = "COMPLETED"
                state.work.enrichment_failure_code = None
                state.work.enrichment_completed_at = completed_at
                state.work.embedding_status = "READY"
                state.work.embedding_failure_code = None
                state.work.embedding_ready_at = completed_at
                await session.flush()
                transactions = await self._dispatch_ready_work(
                    session,
                    trace_id=message.trace_id,
                )
        for transaction in transactions:
            transaction.emit_committed_events()
        return EnrichmentCommitDecision(True, "completed")

    async def fail(
        self,
        message: EnrichChunkMessage,
        *,
        message_id: int,
        failure_code: str,
    ) -> bool:
        transaction: SqlAlchemyPipelineDispatchTransaction | None = None
        async with self._session_factory() as session:
            async with session.begin():
                state = await self._load_state(session, message, lock=True)
                reason = self._rejection_reason(message, message_id, state)
                if reason is not None:
                    self._cancel_if_deleting(reason, state.work)
                    return False
                assert state.video is not None
                assert state.run is not None
                assert state.work is not None
                failed_at = await session.scalar(select(func.now()))
                state.work.enrichment_status = "FAILED"
                state.work.enrichment_failure_code = failure_code
                state.work.enrichment_failed_at = failed_at
                state.run.status = "FAILED"
                state.run.is_active = False
                state.run.failure_code = failure_code
                state.video.status = "FAILED"
                state.video.failed_stage = "ENRICH_CHUNK"
                state.video.failure_code = failure_code
                state.video.failure_trace_id = message.trace_id
                await session.flush()
                transaction = self._new_transaction(session)
                await self._scheduler.dispatch_in_transaction(
                    transaction,
                    "ENRICH_CHUNK",
                    self._enrichment_capacity,
                    trace_id=message.trace_id,
                )
        if transaction is not None:
            transaction.emit_committed_events()
        return True

    async def _load_state(
        self,
        session: AsyncSession,
        message: EnrichChunkMessage,
        *,
        lock: bool,
    ) -> _LockedEnrichmentState:
        video = await session.get(
            VideoModel,
            message.video_id,
            with_for_update=True if lock else None,
        )
        run = await session.get(
            PipelineRunModel,
            message.pipeline_run_id,
            with_for_update=True if lock else None,
        )
        work = await session.get(
            PipelineChunkWorkModel,
            message.chunk_work_id,
            with_for_update=True if lock else None,
        )
        return _LockedEnrichmentState(video=video, run=run, work=work)

    @staticmethod
    def _rejection_reason(
        message: EnrichChunkMessage,
        message_id: int,
        state: _LockedEnrichmentState,
    ) -> str | None:
        if state.video is None:
            return "video_not_found"
        if state.video.status == "DELETING":
            return "video_deleting"
        if (
            state.run is None
            or not state.run.is_active
            or state.run.video_id != message.video_id
        ):
            return "inactive_pipeline_run"
        if state.work is None or state.work.pipeline_run_id != message.pipeline_run_id:
            return "work_not_found"
        if not SqlAlchemyEnrichmentRepository._identity_matches(message, state.work):
            return "identity_mismatch"
        if state.work.enrichment_message_id != message_id:
            return "stale_message_id"
        if state.work.enrichment_status == "COMPLETED":
            return "already_completed"
        if state.work.enrichment_status != "RUNNING" or state.work.frame_ref is None:
            return "terminal_or_not_running"
        return None

    @staticmethod
    def _identity_matches(
        message: EnrichChunkMessage,
        work: PipelineChunkWorkModel,
    ) -> bool:
        return (
            work.chunk_work_id == message.chunk_work_id
            and work.chunk_index == message.chunk_index
            and work.chunking_version == message.chunking_version
            and work.stt_model_version == message.stt_model_version
        )

    @staticmethod
    def _cancel_if_deleting(
        reason: str,
        work: PipelineChunkWorkModel | None,
    ) -> None:
        if reason == "video_deleting" and work is not None:
            work.enrichment_status = "CANCELLED"
            work.enrichment_cancelled_at = func.now()

    @staticmethod
    def _asset_id(message: EnrichChunkMessage) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"biblio:keyframe:{message.pipeline_run_id}:{message.chunk_index}",
        )

    @staticmethod
    def _chunk_id(message: EnrichChunkMessage) -> UUID:
        return uuid5(NAMESPACE_URL, f"biblio:chunk:{message.chunk_work_id}")

    def _persist_result(
        self,
        session: AsyncSession,
        message: EnrichChunkMessage,
        *,
        work: PipelineChunkWorkModel,
        keyframe_ref: str,
        vision_result: VisionResult,
        enriched_text: str,
    ) -> None:
        asset_id = self._asset_id(message)
        chunk_id = self._chunk_id(message)
        session.add(
            AssetModel(
                id=asset_id,
                video_id=message.video_id,
                asset_type="KEYFRAME",
                storage_path=keyframe_ref,
                start_ms=work.start_ms,
                end_ms=work.end_ms,
            )
        )
        session.add(
            ChunkModel(
                id=chunk_id,
                video_id=message.video_id,
                chunk_index=work.chunk_index,
                text=work.text,
                enriched_text=enriched_text,
                start_ms=work.start_ms,
                end_ms=work.end_ms,
                keyframe_asset_id=asset_id,
                chunking_version=work.chunking_version,
                stt_model_version=work.stt_model_version,
                embedding_model_version=work.embedding_model_version,
                visual_caption=vision_result.visual_caption,
                ocr_text=vision_result.ocr_text,
                scene_tags=vision_result.scene_tags,
            )
        )
        work.chunk_id = chunk_id

    def _completed_decision(
        self,
        message: EnrichChunkMessage,
        state: _LockedEnrichmentState,
    ) -> EnrichmentCommitDecision:
        work = state.work
        if work is None or work.chunk_id != self._chunk_id(message):
            return EnrichmentCommitDecision(False, "completed_result_mismatch")
        return EnrichmentCommitDecision(True, "already_completed")

    async def _dispatch_ready_work(
        self,
        session: AsyncSession,
        *,
        trace_id: UUID,
    ) -> list[SqlAlchemyPipelineDispatchTransaction]:
        transactions: list[SqlAlchemyPipelineDispatchTransaction] = []
        for stage, capacity in (
            ("ENRICH_CHUNK", self._enrichment_capacity),
            ("EMBED_BATCH", self._embedding_capacity),
        ):
            transaction = self._new_transaction(session)
            await self._scheduler.dispatch_in_transaction(
                transaction,
                stage,
                capacity,
                trace_id=trace_id,
            )
            transactions.append(transaction)
        return transactions

    def _new_transaction(
        self,
        session: AsyncSession,
    ) -> SqlAlchemyPipelineDispatchTransaction:
        return SqlAlchemyPipelineDispatchTransaction(
            session=session,
            publisher=self._publisher,
            embedding_batch_size=self._embedding_batch_size,
        )
