from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.models import (
    ChunkModel,
    PipelineAudioPartModel,
    PipelineChunkWorkModel,
    PipelineEmbeddingBatchModel,
    PipelineRunModel,
    VectorIndexEntryModel,
    VideoModel,
)
from src.infra.db.pipeline_dispatch_unit_of_work import (
    SqlAlchemyPipelineDispatchTransaction,
    TransactionBoundPublisher,
)
from src.schemas.messages import EmbedBatchMessage
from src.services.embedding_service import (
    EmbeddingBatchInput,
    EmbeddingChunkInput,
    EmbeddingCommitDecision,
)
from src.services.pipeline_work_scheduler import PipelineWorkScheduler


@dataclass(frozen=True, slots=True)
class _ChunkState:
    work: PipelineChunkWorkModel
    run: PipelineRunModel
    video: VideoModel
    chunk: ChunkModel


class SqlAlchemyEmbeddingRepository:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: TransactionBoundPublisher,
        scheduler: PipelineWorkScheduler,
        embedding_capacity: int,
        embedding_batch_size: int = 16,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._scheduler = scheduler
        self._embedding_capacity = embedding_capacity
        self._embedding_batch_size = embedding_batch_size

    async def load_input(
        self,
        message: EmbedBatchMessage,
        *,
        message_id: int,
    ) -> EmbeddingBatchInput | None:
        async with self._session_factory() as session:
            async with session.begin():
                await self._lock_batch_videos(session, message.batch_id)
                batch = await session.get(
                    PipelineEmbeddingBatchModel,
                    message.batch_id,
                    with_for_update=True,
                )
                if not self._batch_matches(message, message_id, batch):
                    return None
                assert batch is not None
                chunks = await self._load_executable_chunks(session, message, batch)
                if not chunks:
                    batch.status = "CANCELLED"
                    batch.cancelled_at = func.now()
                    return None
                return EmbeddingBatchInput(
                    batch_id=batch.batch_id,
                    model_version=batch.embedding_model_version,
                    index_name=batch.index_name,
                    chunks=chunks,
                )

    async def complete(
        self,
        message: EmbedBatchMessage,
        *,
        message_id: int,
        input_record: EmbeddingBatchInput,
        vectors: list[list[float]],
    ) -> EmbeddingCommitDecision:
        transaction: SqlAlchemyPipelineDispatchTransaction | None = None
        async with self._session_factory() as session:
            async with session.begin():
                await self._lock_videos(session, input_record)
                batch = await session.get(
                    PipelineEmbeddingBatchModel,
                    message.batch_id,
                    with_for_update=True,
                )
                if not self._batch_matches(message, message_id, batch):
                    return EmbeddingCommitDecision(False, "stale_batch", 0, 0, ())
                assert batch is not None
                stored_count, touched_runs = await self._store_batch_results(
                    session,
                    message,
                    input_record,
                    vectors,
                )
                await self._finish_batch(session, batch, stored_count=stored_count)
                completed_videos = await self._mark_completed_videos(
                    session,
                    touched_runs,
                )

                await session.flush()
                transaction = self._new_transaction(session)
                await self._scheduler.dispatch_in_transaction(
                    transaction,
                    "EMBED_BATCH",
                    self._embedding_capacity,
                    trace_id=message.trace_id,
                )
        if transaction is not None:
            transaction.emit_committed_events()
        discarded_count = len(input_record.chunks) - stored_count
        return EmbeddingCommitDecision(
            True,
            "completed",
            stored_count,
            discarded_count,
            tuple(completed_videos),
        )

    async def fail(
        self,
        message: EmbedBatchMessage,
        *,
        message_id: int,
        failure_code: str,
    ) -> bool:
        transaction: SqlAlchemyPipelineDispatchTransaction | None = None
        async with self._session_factory() as session:
            async with session.begin():
                await self._lock_batch_videos(session, message.batch_id)
                batch = await session.get(
                    PipelineEmbeddingBatchModel,
                    message.batch_id,
                    with_for_update=True,
                )
                if not self._batch_matches(message, message_id, batch):
                    return False
                assert batch is not None
                failed_at = await session.scalar(select(func.now()))
                batch.status = "FAILED"
                batch.failure_code = failure_code
                batch.failed_at = failed_at
                await self._mark_batch_work_failed(
                    session,
                    message,
                    batch,
                    failure_code=failure_code,
                    failed_at=failed_at,
                )
                await session.flush()
                transaction = self._new_transaction(session)
                await self._scheduler.dispatch_in_transaction(
                    transaction,
                    "EMBED_BATCH",
                    self._embedding_capacity,
                    trace_id=message.trace_id,
                )
        if transaction is not None:
            transaction.emit_committed_events()
        return True

    async def _store_batch_results(
        self,
        session: AsyncSession,
        message: EmbedBatchMessage,
        input_record: EmbeddingBatchInput,
        vectors: list[list[float]],
    ) -> tuple[int, dict[UUID, tuple[PipelineRunModel, VideoModel]]]:
        stored_count = 0
        touched_runs: dict[UUID, tuple[PipelineRunModel, VideoModel]] = {}
        completed_at = await session.scalar(select(func.now()))
        for chunk_input, vector in zip(input_record.chunks, vectors, strict=True):
            state = await self._load_chunk_state(
                session,
                chunk_input.chunk_work_id,
                lock=True,
            )
            if not self._can_store_result(message, input_record, chunk_input, state):
                self._cancel_running_work(state)
                continue
            assert state is not None
            await self._upsert_vector(
                session,
                state,
                index_name=input_record.index_name,
                model_version=input_record.model_version,
                vector=vector,
            )
            state.work.embedding_status = "COMPLETED"
            state.work.embedding_failure_code = None
            state.work.embedding_completed_at = completed_at
            touched_runs[state.run.id] = (state.run, state.video)
            stored_count += 1
        return stored_count, touched_runs

    @staticmethod
    async def _finish_batch(
        session: AsyncSession,
        batch: PipelineEmbeddingBatchModel,
        *,
        stored_count: int,
    ) -> None:
        finished_at = await session.scalar(select(func.now()))
        if stored_count:
            batch.status = "COMPLETED"
            batch.completed_at = finished_at
            return
        batch.status = "CANCELLED"
        batch.cancelled_at = finished_at

    async def _mark_completed_videos(
        self,
        session: AsyncSession,
        touched_runs: dict[UUID, tuple[PipelineRunModel, VideoModel]],
    ) -> list[tuple[UUID, UUID]]:
        completed: list[tuple[UUID, UUID]] = []
        for run, video in touched_runs.values():
            if not await self._is_video_complete(session, run, video):
                continue
            run.status = "COMPLETED"
            run.is_active = False
            video.status = "READY"
            video.failed_stage = None
            video.failure_code = None
            video.failure_trace_id = None
            video.processing_claimed_at = None
            completed.append((video.id, run.id))
        return completed

    async def _mark_batch_work_failed(
        self,
        session: AsyncSession,
        message: EmbedBatchMessage,
        batch: PipelineEmbeddingBatchModel,
        *,
        failure_code: str,
        failed_at: datetime,
    ) -> None:
        for chunk_work_id in self._chunk_work_ids(batch):
            state = await self._load_chunk_state(session, chunk_work_id, lock=True)
            if state is None or state.work.embedding_status != "RUNNING":
                continue
            state.work.embedding_status = "FAILED"
            state.work.embedding_failure_code = failure_code
            state.work.embedding_failed_at = failed_at
            if state.video.status == "DELETING" or not state.run.is_active:
                continue
            state.run.status = "FAILED"
            state.run.is_active = False
            state.run.failure_code = failure_code
            state.video.status = "FAILED"
            state.video.failed_stage = "EMBED_BATCH"
            state.video.failure_code = failure_code
            state.video.failure_trace_id = message.trace_id

    async def _load_executable_chunks(
        self,
        session: AsyncSession,
        message: EmbedBatchMessage,
        batch: PipelineEmbeddingBatchModel,
    ) -> tuple[EmbeddingChunkInput, ...]:
        chunks: list[EmbeddingChunkInput] = []
        for chunk_work_id in self._chunk_work_ids(batch):
            state = await self._load_chunk_state(session, chunk_work_id, lock=True)
            if state is None:
                raise RuntimeError(f"Embedding chunk state missing: {chunk_work_id}")
            if not self._state_is_executable(message, state):
                self._cancel_running_work(state)
                continue
            chunks.append(
                EmbeddingChunkInput(
                    chunk_work_id=state.work.chunk_work_id,
                    chunk_id=state.chunk.id,
                    pipeline_run_id=state.run.id,
                    video_id=state.video.id,
                    enriched_text=state.chunk.enriched_text,
                )
            )
        return tuple(chunks)

    @staticmethod
    async def _load_chunk_state(
        session: AsyncSession,
        chunk_work_id: UUID,
        *,
        lock: bool,
    ) -> _ChunkState | None:
        work = await session.get(
            PipelineChunkWorkModel,
            chunk_work_id,
            with_for_update=True if lock else None,
        )
        if work is None or work.chunk_id is None:
            return None
        run = await session.get(
            PipelineRunModel,
            work.pipeline_run_id,
            with_for_update=True if lock else None,
        )
        chunk = await session.get(ChunkModel, work.chunk_id)
        if run is None or chunk is None:
            return None
        video = await session.get(
            VideoModel,
            run.video_id,
            with_for_update=True if lock else None,
        )
        if video is None:
            return None
        return _ChunkState(work=work, run=run, video=video, chunk=chunk)

    @staticmethod
    def _batch_matches(
        message: EmbedBatchMessage,
        message_id: int,
        batch: PipelineEmbeddingBatchModel | None,
    ) -> bool:
        return bool(
            batch is not None
            and batch.message_id == message_id
            and batch.status == "RUNNING"
            and batch.embedding_model_version == message.embedding_model_version
            and batch.index_name == message.index_name
        )

    @staticmethod
    def _state_is_executable(
        message: EmbedBatchMessage,
        state: _ChunkState,
    ) -> bool:
        return (
            state.video.status != "DELETING"
            and state.run.is_active
            and state.work.pipeline_run_id == state.run.id
            and state.work.embedding_batch_id == message.batch_id
            and state.work.embedding_status == "RUNNING"
            and state.work.embedding_model_version == message.embedding_model_version
            and state.work.index_name == message.index_name
            and state.work.chunk_id == state.chunk.id
        )

    @staticmethod
    def _can_store_result(
        message: EmbedBatchMessage,
        input_record: EmbeddingBatchInput,
        chunk_input: EmbeddingChunkInput,
        state: _ChunkState | None,
    ) -> bool:
        return bool(
            state is not None
            and SqlAlchemyEmbeddingRepository._state_is_executable(message, state)
            and state.work.chunk_id == chunk_input.chunk_id
            and state.work.pipeline_run_id == chunk_input.pipeline_run_id
            and state.video.id == chunk_input.video_id
            and input_record.model_version == state.work.embedding_model_version
            and input_record.index_name == state.work.index_name
        )

    @staticmethod
    def _cancel_running_work(state: _ChunkState | None) -> None:
        if state is None or state.work.embedding_status != "RUNNING":
            return
        if state.video.status == "DELETING" or not state.run.is_active:
            state.work.embedding_status = "CANCELLED"
            state.work.embedding_cancelled_at = func.now()

    @staticmethod
    async def _upsert_vector(
        session: AsyncSession,
        state: _ChunkState,
        *,
        index_name: str,
        model_version: str,
        vector: list[float],
    ) -> None:
        identity = {"index_name": index_name, "chunk_id": state.chunk.id}
        existing = await session.get(
            VectorIndexEntryModel,
            identity,
            with_for_update=True,
        )
        if existing is None:
            session.add(
                VectorIndexEntryModel(
                    index_name=index_name,
                    chunk_id=state.chunk.id,
                    user_id=state.video.user_id,
                    project_id=state.video.project_id,
                    video_id=state.video.id,
                    embedding_vector=vector,
                    embedding_model_version=model_version,
                )
            )
            return
        if existing.embedding_model_version != model_version:
            raise RuntimeError(
                "Existing vector model version does not match embedding batch"
            )

    @staticmethod
    async def _is_video_complete(
        session: AsyncSession,
        run: PipelineRunModel,
        video: VideoModel,
    ) -> bool:
        if not SqlAlchemyEmbeddingRepository._run_state_is_complete(run, video):
            return False
        if not await SqlAlchemyEmbeddingRepository._all_parts_completed(session, run):
            return False
        return await SqlAlchemyEmbeddingRepository._all_chunk_vectors_completed(
            session,
            run,
            video,
        )

    @staticmethod
    def _run_state_is_complete(
        run: PipelineRunModel,
        video: VideoModel,
    ) -> bool:
        if (
            video.status in {"DELETING", "FAILED"}
            or not run.is_active
            or run.status != "RUNNING"
            or run.failure_code is not None
            or not run.normalization_completed
            or run.normalization_status != "COMPLETED"
            or not run.transcript_completed
            or not run.assembly_completed
            or run.total_part_count is None
            or run.next_part_index != run.total_part_count
            or bool(run.pending_words)
            or bool(run.chunk_buffer)
        ):
            return False
        return True

    @staticmethod
    async def _all_parts_completed(
        session: AsyncSession,
        run: PipelineRunModel,
    ) -> bool:
        expected_count = run.total_part_count
        if expected_count is None:
            return False
        part_statuses = list(
            await session.scalars(
                select(PipelineAudioPartModel.status).where(
                    PipelineAudioPartModel.pipeline_run_id == run.id
                )
            )
        )
        if len(part_statuses) != expected_count or any(
            status != "COMPLETED" for status in part_statuses
        ):
            return False
        return True

    @staticmethod
    async def _all_chunk_vectors_completed(
        session: AsyncSession,
        run: PipelineRunModel,
        video: VideoModel,
    ) -> bool:
        works = list(
            await session.scalars(
                select(PipelineChunkWorkModel).where(
                    PipelineChunkWorkModel.pipeline_run_id == run.id
                )
            )
        )
        if not works or any(
            work.enrichment_status != "COMPLETED"
            or work.embedding_status != "COMPLETED"
            or work.chunk_id is None
            for work in works
        ):
            return False

        vector_keys = {
            (entry.index_name, entry.chunk_id, entry.embedding_model_version)
            for entry in await session.scalars(
                select(VectorIndexEntryModel).where(
                    VectorIndexEntryModel.video_id == video.id
                )
            )
        }
        return all(
            (work.index_name, work.chunk_id, work.embedding_model_version)
            in vector_keys
            for work in works
        )

    @staticmethod
    async def _lock_batch_videos(
        session: AsyncSession,
        batch_id: UUID,
    ) -> None:
        await session.execute(
            select(VideoModel.id)
            .join(PipelineRunModel, PipelineRunModel.video_id == VideoModel.id)
            .join(
                PipelineChunkWorkModel,
                PipelineChunkWorkModel.pipeline_run_id == PipelineRunModel.id,
            )
            .where(PipelineChunkWorkModel.embedding_batch_id == batch_id)
            .order_by(VideoModel.id)
            .with_for_update()
        )

    @staticmethod
    async def _lock_videos(
        session: AsyncSession,
        input_record: EmbeddingBatchInput,
    ) -> None:
        video_ids = sorted({chunk.video_id for chunk in input_record.chunks})
        if video_ids:
            await session.execute(
                select(VideoModel.id)
                .where(VideoModel.id.in_(video_ids))
                .order_by(VideoModel.id)
                .with_for_update()
            )

    @staticmethod
    def _chunk_work_ids(batch: PipelineEmbeddingBatchModel) -> list[UUID]:
        return [UUID(value) for value in batch.chunk_work_ids]

    def _new_transaction(
        self,
        session: AsyncSession,
    ) -> SqlAlchemyPipelineDispatchTransaction:
        return SqlAlchemyPipelineDispatchTransaction(
            session=session,
            publisher=self._publisher,
            embedding_batch_size=self._embedding_batch_size,
        )
