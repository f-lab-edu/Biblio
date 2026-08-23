from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.infra.ai.google_stt_adapter import (
    ExternalAIAdapterError,
    GoogleSTTAdapter,
)
from src.infra.db.transcription_repository import (
    TranscriptionCommitDecision,
    TranscriptionInput,
)
from src.infra.queue.consumer import StageDispatchContext
from src.infra.storage.inmemory_storage import InMemoryStorageClient
from src.schemas.messages import MessageType, TranscribePartMessage
from src.services.transcription_artifact import (
    TranscriptionArtifact,
    transcription_result_path,
)
from src.services.transcription_service import TranscriptionService


class _Repository:
    def __init__(self, input_record: TranscriptionInput) -> None:
        self.input_record = input_record
        self.decision = TranscriptionCommitDecision(True, "completed")
        self.completed: list[tuple[int, str, TranscriptionArtifact]] = []
        self.failures: list[tuple[int, str]] = []

    async def load_input(self, message, *, message_id):
        del message, message_id
        return self.input_record

    async def complete(self, message, *, message_id, result_ref, artifact):
        del message
        self.completed.append((message_id, result_ref, artifact))
        return self.decision

    async def fail(self, message, *, message_id, failure_code):
        del message
        self.failures.append((message_id, failure_code))
        return True


def _context(*, read_count: int = 1) -> StageDispatchContext:
    message = TranscribePartMessage(
        message_type=MessageType.TRANSCRIBE_PART,
        payload_version="v1",
        trace_id=uuid4(),
        attempt=1,
        pipeline_run_id=uuid4(),
        video_id=uuid4(),
        audio_part_id=uuid4(),
        part_index=0,
        stt_model_version="chirp_3",
        issued_at=datetime.now(UTC),
    )
    now = datetime.now(UTC)
    return StageDispatchContext(
        message=message,
        message_id=41,
        read_count=read_count,
        enqueued_at=now,
        queue_name="TRANSCRIBE_PART",
        started_at=now,
        queue_wait_ms=0,
    )


def _input() -> TranscriptionInput:
    return TranscriptionInput(
        audio_gcs_path="audio/part-000.flac",
        start_ms=0,
        end_ms=60_000,
    )


def _adapter(*, calls: list[str], error: Exception | None = None) -> GoogleSTTAdapter:
    async def client(audio_uri: str, trace_id: str):
        del trace_id
        calls.append(audio_uri)
        if error is not None:
            raise error
        return {
            "stt_model_version": "chirp_3",
            "segments": [{"text": "안녕.", "start_ms": 0, "end_ms": 500}],
            "words": [{"text": "안녕.", "start_ms": 0, "end_ms": 500}],
        }

    return GoogleSTTAdapter(client, max_retries=0)


@pytest.mark.asyncio
async def test_uploads_result_then_commits_part() -> None:
    context = _context()
    repository = _Repository(_input())
    storage = InMemoryStorageClient({"audio/part-000.flac": b"flac"})
    calls: list[str] = []
    service = TranscriptionService(
        repository=repository,
        storage=storage,
        stt=_adapter(calls=calls),
        max_delivery_attempts=3,
    )

    result = await service.execute(context)

    expected_ref = transcription_result_path(
        context.message.video_id,
        context.message.pipeline_run_id,
        0,
    )
    assert result.outcome == "SUCCEEDED"
    assert calls == ["gs://test-bucket/audio/part-000.flac"]
    assert expected_ref in storage.objects
    assert repository.completed[0][1] == expected_ref


@pytest.mark.asyncio
async def test_reuses_uploaded_result_without_calling_stt() -> None:
    context = _context(read_count=2)
    input_record = _input()
    repository = _Repository(input_record)
    result_ref = transcription_result_path(
        context.message.video_id,
        context.message.pipeline_run_id,
        0,
    )
    artifact = TranscriptionArtifact.from_result(
        pipeline_run_id=context.message.pipeline_run_id,
        audio_part_id=context.message.audio_part_id,
        part_index=0,
        start_ms=input_record.start_ms,
        end_ms=input_record.end_ms,
        result=await _adapter(calls=[]).transcribe(
            audio_uri="gs://bucket/input.flac",
            trace_id=str(context.message.trace_id),
        ),
    )
    storage = InMemoryStorageClient({result_ref: artifact.to_bytes()})
    calls: list[str] = []
    service = TranscriptionService(
        repository=repository,
        storage=storage,
        stt=_adapter(calls=calls),
        max_delivery_attempts=3,
    )

    result = await service.execute(context)

    assert result.outcome == "SUCCEEDED"
    assert result.reused is True
    assert calls == []


@pytest.mark.asyncio
async def test_concurrent_upload_reuses_first_result_without_overwriting() -> None:
    context = _context()
    input_record = _input()
    repository = _Repository(input_record)
    result_ref = transcription_result_path(
        context.message.video_id,
        context.message.pipeline_run_id,
        0,
    )
    canonical = TranscriptionArtifact.from_result(
        pipeline_run_id=context.message.pipeline_run_id,
        audio_part_id=context.message.audio_part_id,
        part_index=0,
        start_ms=input_record.start_ms,
        end_ms=input_record.end_ms,
        result=await _adapter(calls=[]).transcribe(
            audio_uri="gs://bucket/input.flac",
            trace_id=str(context.message.trace_id),
        ),
    )

    class _RacingStorage(InMemoryStorageClient):
        async def upload_object_if_absent(self, source, storage_path):
            del source
            self.objects[storage_path] = canonical.to_bytes()
            return False

    storage = _RacingStorage({input_record.audio_gcs_path: b"flac"})
    calls: list[str] = []
    service = TranscriptionService(
        repository=repository,
        storage=storage,
        stt=_adapter(calls=calls),
        max_delivery_attempts=3,
    )

    result = await service.execute(context)

    assert result.outcome == "SUCCEEDED"
    assert result.reused is True
    assert calls == ["gs://test-bucket/audio/part-000.flac"]
    assert storage.objects[result_ref] == canonical.to_bytes()


@pytest.mark.asyncio
async def test_stale_message_keeps_result_for_current_delivery_to_reuse() -> None:
    context = _context()
    repository = _Repository(_input())
    repository.decision = TranscriptionCommitDecision(False, "stale_message_id")
    storage = InMemoryStorageClient()
    service = TranscriptionService(
        repository=repository,
        storage=storage,
        stt=_adapter(calls=[]),
        max_delivery_attempts=3,
    )

    result = await service.execute(context)

    result_ref = transcription_result_path(
        context.message.video_id,
        context.message.pipeline_run_id,
        0,
    )
    assert result.outcome == "SKIPPED"
    assert result_ref in storage.objects
    assert storage.deleted_paths == []


@pytest.mark.asyncio
async def test_deletion_after_stt_discards_result_and_audio() -> None:
    context = _context()
    input_record = _input()
    repository = _Repository(input_record)
    repository.decision = TranscriptionCommitDecision(False, "video_deleting")
    storage = InMemoryStorageClient({input_record.audio_gcs_path: b"flac"})
    service = TranscriptionService(
        repository=repository,
        storage=storage,
        stt=_adapter(calls=[]),
        max_delivery_attempts=3,
    )

    result = await service.execute(context)

    assert result.outcome == "SKIPPED"
    assert input_record.audio_gcs_path not in storage.objects
    assert len(storage.deleted_paths) == 2


@pytest.mark.asyncio
async def test_retryable_failure_is_left_for_redelivery_before_limit() -> None:
    context = _context(read_count=1)
    repository = _Repository(_input())
    error = ExternalAIAdapterError(
        code="UNAVAILABLE",
        message="temporary",
        trace_id=str(context.message.trace_id),
        provider="google-stt",
        retryable=True,
    )
    service = TranscriptionService(
        repository=repository,
        storage=InMemoryStorageClient(),
        stt=_adapter(calls=[], error=error),
        max_delivery_attempts=3,
    )

    with pytest.raises(ExternalAIAdapterError):
        await service.execute(context)

    assert repository.failures == []


@pytest.mark.asyncio
async def test_nonretryable_failure_marks_part_failed() -> None:
    context = _context(read_count=1)
    repository = _Repository(_input())
    error = ExternalAIAdapterError(
        code="INVALID_REQUEST",
        message="bad input",
        trace_id=str(context.message.trace_id),
        provider="google-stt",
        retryable=False,
    )
    service = TranscriptionService(
        repository=repository,
        storage=InMemoryStorageClient(),
        stt=_adapter(calls=[], error=error),
        max_delivery_attempts=3,
    )

    result = await service.execute(context)

    assert result.outcome == "FAILED"
    assert repository.failures == [(41, "INVALID_REQUEST")]


@pytest.mark.asyncio
async def test_retryable_failure_at_delivery_limit_marks_part_failed() -> None:
    context = _context(read_count=3)
    repository = _Repository(_input())
    error = ExternalAIAdapterError(
        code="UNAVAILABLE",
        message="temporary",
        trace_id=str(context.message.trace_id),
        provider="google-stt",
        retryable=True,
    )
    service = TranscriptionService(
        repository=repository,
        storage=InMemoryStorageClient(),
        stt=_adapter(calls=[], error=error),
        max_delivery_attempts=3,
    )

    result = await service.execute(context)

    assert result.outcome == "FAILED"
    assert repository.failures == [(41, "UNAVAILABLE")]
