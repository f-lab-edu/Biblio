from dataclasses import dataclass
from time import perf_counter
from typing import Protocol
from uuid import UUID

from loguru import logger

from src.infra.ai.embedding_client import EmbeddingBatchResult
from src.infra.ai.google_stt_adapter import ExternalAIAdapterError
from src.infra.queue.consumer import StageDispatchContext, StageHandlerResult
from src.schemas.messages import EmbedBatchMessage
from src.telemetry.pipeline_events import (
    PipelineWorkLogContext,
    emit_pipeline_work_event,
    work_log_context_from_message,
)


@dataclass(frozen=True, slots=True)
class EmbeddingChunkInput:
    chunk_work_id: UUID
    chunk_id: UUID
    pipeline_run_id: UUID
    video_id: UUID
    enriched_text: str


@dataclass(frozen=True, slots=True)
class EmbeddingBatchInput:
    batch_id: UUID
    model_version: str
    index_name: str
    chunks: tuple[EmbeddingChunkInput, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingCommitDecision:
    accepted: bool
    reason: str
    stored_count: int
    discarded_count: int
    completed_videos: tuple[tuple[UUID, UUID], ...]


class EmbeddingRepository(Protocol):
    async def load_input(
        self,
        message: EmbedBatchMessage,
        *,
        message_id: int,
    ) -> EmbeddingBatchInput | None: ...

    async def complete(
        self,
        message: EmbedBatchMessage,
        *,
        message_id: int,
        input_record: EmbeddingBatchInput,
        vectors: list[list[float]],
    ) -> EmbeddingCommitDecision: ...

    async def fail(
        self,
        message: EmbedBatchMessage,
        *,
        message_id: int,
        failure_code: str,
    ) -> bool: ...


class EmbeddingAdapter(Protocol):
    async def embed_texts(
        self,
        texts: list[str],
        *,
        trace_id: str,
        model_version: str | None = None,
    ) -> EmbeddingBatchResult: ...


class EmbeddingBatchService:
    def __init__(
        self,
        *,
        repository: EmbeddingRepository,
        embedding: EmbeddingAdapter,
        max_delivery_attempts: int,
    ) -> None:
        self._repository = repository
        self._embedding = embedding
        self._max_delivery_attempts = max_delivery_attempts

    async def execute(self, context: StageDispatchContext) -> StageHandlerResult:
        message = context.message
        if not isinstance(message, EmbedBatchMessage):
            raise TypeError("EMBED_BATCH handler received a different message type")
        log_context = work_log_context_from_message(
            message,
            message_id=context.message_id,
            read_ct=context.read_count,
        )
        input_record = await self._repository.load_input(
            message,
            message_id=context.message_id,
        )
        if input_record is None:
            return StageHandlerResult("SKIPPED", reason="stale_after_claim")

        started_at = perf_counter()
        try:
            result = await self._embedding.embed_texts(
                [chunk.enriched_text for chunk in input_record.chunks],
                trace_id=str(message.trace_id),
                model_version=input_record.model_version,
            )
            self._validate_result(message, input_record, result)
        except Exception as error:
            return await self._handle_failure(context, error=error)
        endpoint_ms = (perf_counter() - started_at) * 1000

        decision = await self._repository.complete(
            message,
            message_id=context.message_id,
            input_record=input_record,
            vectors=result.embeddings,
        )
        if not decision.accepted:
            return StageHandlerResult("SKIPPED", reason=decision.reason)

        logger.bind(
            log_schema_version=2,
            event_name="embedding.batch.completed",
            **log_context.fields(),
            batch_size=len(input_record.chunks),
            participant_run_ids=[
                str(run_id)
                for run_id in dict.fromkeys(
                    chunk.pipeline_run_id for chunk in input_record.chunks
                )
            ],
            stored_vector_count=decision.stored_count,
            discarded_vector_count=decision.discarded_count,
            embedding_endpoint_ms=endpoint_ms,
        ).info("embedding.batch.completed")
        self._emit_video_completed_events(message, decision)
        return StageHandlerResult("SUCCEEDED")

    @staticmethod
    def _validate_result(
        message: EmbedBatchMessage,
        input_record: EmbeddingBatchInput,
        result: EmbeddingBatchResult,
    ) -> None:
        if result.model_version != input_record.model_version:
            raise ExternalAIAdapterError(
                code="MODEL_VERSION_MISMATCH",
                message="Embedding response model version does not match batch",
                trace_id=str(message.trace_id),
                provider="embedding-endpoint",
                retryable=False,
            )
        if len(result.embeddings) != len(input_record.chunks):
            raise ExternalAIAdapterError(
                code="EMBEDDING_COUNT_MISMATCH",
                message="Embedding response count does not match batch",
                trace_id=str(message.trace_id),
                provider="embedding-endpoint",
                retryable=False,
            )

    async def _handle_failure(
        self,
        context: StageDispatchContext,
        *,
        error: Exception,
    ) -> StageHandlerResult:
        message = context.message
        assert isinstance(message, EmbedBatchMessage)
        retryable = not isinstance(error, ExternalAIAdapterError) or error.retryable
        if retryable and context.read_count < self._max_delivery_attempts:
            raise error
        failure_code = (
            error.code
            if isinstance(error, ExternalAIAdapterError)
            else type(error).__name__
        )
        failed = await self._repository.fail(
            message,
            message_id=context.message_id,
            failure_code=failure_code,
        )
        if not failed:
            return StageHandlerResult("SKIPPED", reason="stale_during_failure")
        return StageHandlerResult("FAILED", failure_code=failure_code)

    @staticmethod
    def _emit_video_completed_events(
        message: EmbedBatchMessage,
        decision: EmbeddingCommitDecision,
    ) -> None:
        for video_id, run_id in decision.completed_videos:
            emit_pipeline_work_event(
                "pipeline.video.completed",
                PipelineWorkLogContext(
                    trace_id=str(message.trace_id),
                    video_id=str(video_id),
                    pipeline_run_id=str(run_id),
                    stage="EMBED_BATCH",
                    work_id=str(message.batch_id),
                    work_attempt=message.attempt,
                    batch_id=str(message.batch_id),
                ),
            )
