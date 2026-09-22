from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from src.infra.ai.vision_adapter import VisionResult
from src.infra.db.enrichment_repository import SqlAlchemyEnrichmentRepository
from src.infra.queue.consumer import StageDispatchContext
from src.infra.storage.inmemory_storage import InMemoryStorageClient
from src.schemas.messages import EnrichChunkMessage, MessageType
from src.services.enrichment_service import (
    EnrichmentCommitDecision,
    EnrichmentInput,
    EnrichmentService,
    keyframe_storage_path,
)


class _Repository:
    def __init__(self, input_record: EnrichmentInput | None) -> None:
        self.input_record = input_record
        self.decision = EnrichmentCommitDecision(True, "completed")
        self.completions: list[dict[str, object]] = []
        self.failures: list[tuple[int, str]] = []

    async def load_input(self, message, *, message_id):
        del message, message_id
        return self.input_record

    async def complete(self, message, **values):
        del message
        self.completions.append(values)
        return self.decision

    async def fail(self, message, *, message_id, failure_code):
        del message
        self.failures.append((message_id, failure_code))
        return True


class _Vision:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.paths: list[str] = []

    async def extract_caption(self, keyframe_path: str, *, trace_id: str) -> str:
        del trace_id
        self.paths.append(keyframe_path)
        if self.error is not None:
            raise self.error
        return "화면 설명"

    async def extract_ocr(self, keyframe_path: str, *, trace_id: str) -> str:
        del keyframe_path, trace_id
        return "화면 글자"

    async def extract_scene_tags(self, keyframe_path: str, *, trace_id: str) -> str:
        del keyframe_path, trace_id
        return "강의"


def _context(*, read_count: int = 1) -> StageDispatchContext:
    message = EnrichChunkMessage(
        message_type=MessageType.ENRICH_CHUNK,
        payload_version="v1",
        trace_id=uuid4(),
        attempt=1,
        pipeline_run_id=uuid4(),
        video_id=uuid4(),
        chunk_work_id=uuid4(),
        chunk_index=3,
        chunking_version="chunk-v1",
        stt_model_version="chirp-3",
        issued_at=datetime.now(UTC),
    )
    now = datetime.now(UTC)
    return StageDispatchContext(
        message=message,
        message_id=71,
        read_count=read_count,
        enqueued_at=now,
        queue_name="ENRICH_CHUNK",
        started_at=now,
        queue_wait_ms=0,
    )


def _input() -> EnrichmentInput:
    return EnrichmentInput(
        text="원래 자막",
        start_ms=1_000,
        end_ms=8_000,
        frame_ref="candidates/frame-003.jpg",
    )


def _service(repository, storage, vision, *, max_deliveries: int = 3):
    return EnrichmentService(
        repository=repository,
        storage=storage,
        vision=vision,
        vision_max_retries=0,
        max_delivery_attempts=max_deliveries,
    )


@pytest.mark.asyncio
async def test_enriches_candidate_and_cleans_local_file() -> None:
    context = _context()
    repository = _Repository(_input())
    storage = InMemoryStorageClient({_input().frame_ref: b"jpeg"})
    vision = _Vision()

    result = await _service(repository, storage, vision).execute(context)

    expected_ref = keyframe_storage_path(
        context.message.video_id,
        context.message.pipeline_run_id,
        context.message.chunk_index,
    )
    assert result.outcome == "SUCCEEDED"
    assert storage.objects[expected_ref] == b"jpeg"
    assert repository.completions[0]["keyframe_ref"] == expected_ref
    assert repository.completions[0]["vision_result"] == VisionResult(
        visual_caption="화면 설명",
        ocr_text="화면 글자",
        scene_tags="강의",
    )
    assert repository.completions[0]["enriched_text"] == (
        "원래 자막 화면 설명 화면 글자 강의"
    )
    assert vision.paths and not Path(vision.paths[0]).exists()


@pytest.mark.asyncio
async def test_stale_after_claim_skips_external_work() -> None:
    repository = _Repository(None)
    storage = InMemoryStorageClient()
    vision = _Vision()

    result = await _service(repository, storage, vision).execute(_context())

    assert result.outcome == "SKIPPED"
    assert result.reason == "stale_after_claim"
    assert not vision.paths
    assert not repository.completions


@pytest.mark.asyncio
async def test_deletion_after_vision_discards_new_keyframe() -> None:
    context = _context()
    repository = _Repository(_input())
    repository.decision = EnrichmentCommitDecision(False, "video_deleting")
    storage = InMemoryStorageClient({_input().frame_ref: b"jpeg"})

    result = await _service(repository, storage, _Vision()).execute(context)

    expected_ref = keyframe_storage_path(
        context.message.video_id,
        context.message.pipeline_run_id,
        context.message.chunk_index,
    )
    assert result.outcome == "SKIPPED"
    assert expected_ref not in storage.objects
    assert expected_ref in storage.deleted_paths


@pytest.mark.asyncio
async def test_retryable_vision_failure_is_not_committed() -> None:
    repository = _Repository(_input())
    storage = InMemoryStorageClient({_input().frame_ref: b"jpeg"})

    with pytest.raises(RuntimeError, match="vision unavailable"):
        await _service(
            repository,
            storage,
            _Vision(error=RuntimeError("vision unavailable")),
        ).execute(_context(read_count=1))

    assert not repository.completions
    assert not repository.failures


@pytest.mark.asyncio
async def test_last_delivery_marks_work_failed() -> None:
    repository = _Repository(_input())
    storage = InMemoryStorageClient({_input().frame_ref: b"jpeg"})

    result = await _service(
        repository,
        storage,
        _Vision(error=RuntimeError("vision unavailable")),
        max_deliveries=3,
    ).execute(_context(read_count=3))

    assert result.outcome == "FAILED"
    assert result.failure_code == "RuntimeError"
    assert repository.failures == [(71, "RuntimeError")]


def test_keyframe_and_chunk_ids_are_stable() -> None:
    context = _context()

    assert keyframe_storage_path(
        context.message.video_id,
        context.message.pipeline_run_id,
        3,
    ).endswith("keyframes/chunk-000003.jpg")
    assert SqlAlchemyEnrichmentRepository._asset_id(
        context.message
    ) == SqlAlchemyEnrichmentRepository._asset_id(context.message)
    assert SqlAlchemyEnrichmentRepository._chunk_id(
        context.message
    ) == SqlAlchemyEnrichmentRepository._chunk_id(context.message)
