from datetime import UTC, datetime
from uuid import UUID

import pytest

from src.infra.ai.embedding_client import EmbeddingBatchResult
from src.infra.ai.google_stt_adapter import ExternalAIAdapterError
from src.infra.queue.consumer import StageDispatchContext
from src.schemas.messages import EmbedBatchMessage, MessageType
from src.services.embedding_service import (
    EmbeddingBatchInput,
    EmbeddingBatchService,
    EmbeddingChunkInput,
    EmbeddingCommitDecision,
)


BATCH_ID = UUID("10000000-0000-0000-0000-000000000001")
RUN_ID = UUID("20000000-0000-0000-0000-000000000001")
VIDEO_ID = UUID("30000000-0000-0000-0000-000000000001")
WORK_ID = UUID("40000000-0000-0000-0000-000000000001")
CHUNK_ID = UUID("50000000-0000-0000-0000-000000000001")
TRACE_ID = UUID("60000000-0000-0000-0000-000000000001")


def _message() -> EmbedBatchMessage:
    return EmbedBatchMessage(
        message_type=MessageType.EMBED_BATCH,
        payload_version="v1",
        trace_id=TRACE_ID,
        attempt=1,
        batch_id=BATCH_ID,
        embedding_model_version="model-v1",
        index_name="video-chunks",
        issued_at=datetime(2026, 8, 24, tzinfo=UTC),
    )


def _input() -> EmbeddingBatchInput:
    return EmbeddingBatchInput(
        batch_id=BATCH_ID,
        model_version="model-v1",
        index_name="video-chunks",
        chunks=(
            EmbeddingChunkInput(
                chunk_work_id=WORK_ID,
                chunk_id=CHUNK_ID,
                pipeline_run_id=RUN_ID,
                video_id=VIDEO_ID,
                enriched_text="caption and transcript",
            ),
        ),
    )


def _context(*, read_count: int = 1) -> StageDispatchContext:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    return StageDispatchContext(
        message=_message(),
        message_id=91,
        read_count=read_count,
        enqueued_at=now,
        queue_name="EMBED_BATCH",
        started_at=now,
        queue_wait_ms=0,
    )


class _Repository:
    def __init__(self, input_record: EmbeddingBatchInput | None = None) -> None:
        self.input_record = input_record
        self.completed_vectors: list[list[float]] | None = None
        self.failure_code: str | None = None

    async def load_input(self, message, *, message_id):
        del message, message_id
        return self.input_record

    async def complete(self, message, *, message_id, input_record, vectors):
        del message, message_id, input_record
        self.completed_vectors = vectors
        return EmbeddingCommitDecision(True, "completed", 1, 0, ())

    async def fail(self, message, *, message_id, failure_code):
        del message, message_id
        self.failure_code = failure_code
        return True


class _Embedding:
    def __init__(self, result: EmbeddingBatchResult | Exception) -> None:
        self.result = result
        self.texts: list[str] = []

    async def embed_texts(self, texts, *, trace_id, model_version=None):
        del trace_id, model_version
        self.texts = texts
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_embeds_ordered_text_and_commits_vectors() -> None:
    repository = _Repository(_input())
    embedding = _Embedding(EmbeddingBatchResult([[0.1, 0.2]], "model-v1"))
    service = EmbeddingBatchService(
        repository=repository,
        embedding=embedding,
        max_delivery_attempts=3,
    )

    result = await service.execute(_context())

    assert result.outcome == "SUCCEEDED"
    assert embedding.texts == ["caption and transcript"]
    assert repository.completed_vectors == [[0.1, 0.2]]


@pytest.mark.asyncio
async def test_skips_when_batch_is_stale_after_claim() -> None:
    service = EmbeddingBatchService(
        repository=_Repository(None),
        embedding=_Embedding(EmbeddingBatchResult([], "model-v1")),
        max_delivery_attempts=3,
    )

    result = await service.execute(_context())

    assert result.outcome == "SKIPPED"
    assert result.reason == "stale_after_claim"


@pytest.mark.asyncio
async def test_final_non_retryable_failure_marks_batch_failed() -> None:
    repository = _Repository(_input())
    embedding = _Embedding(
        ExternalAIAdapterError(
            code="INVALID_RESPONSE",
            message="bad response",
            trace_id=str(TRACE_ID),
            provider="embedding-endpoint",
            retryable=False,
        )
    )
    service = EmbeddingBatchService(
        repository=repository,
        embedding=embedding,
        max_delivery_attempts=3,
    )

    result = await service.execute(_context())

    assert result.outcome == "FAILED"
    assert repository.failure_code == "INVALID_RESPONSE"
