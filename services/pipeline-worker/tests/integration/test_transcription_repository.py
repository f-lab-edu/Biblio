from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.infra.ai.google_stt_adapter import (
    STTTranscriptionResult,
    TranscriptSegmentDTO,
    TranscriptWordDTO,
)
from src.infra.db.models import PipelineAudioPartModel, PipelineRunModel, VideoModel
from src.infra.db.pipeline_dispatch_unit_of_work import (
    SqlAlchemyPipelineDispatchUnitOfWork,
)
from src.infra.db.transcription_repository import TranscriptionRepository
from src.schemas.messages import MessageType, TranscribePartMessage
from src.services.pipeline_work_scheduler import PipelineWorkScheduler
from src.services.transcription_artifact import TranscriptionArtifact


class _Publisher:
    async def send(self, session, queue_name, payload) -> int:
        del session, queue_name, payload
        return 99


class _AssemblyBoundary:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.seen_run_ids = []

    async def advance_in_transaction(
        self,
        session,
        *,
        pipeline_run_id,
        trace_id,
    ) -> None:
        del session, trace_id
        self.seen_run_ids.append(pipeline_run_id)
        if self.fail:
            raise RuntimeError("assembly boundary failed")


async def _seed_running_part(session_factory):
    video_id = uuid4()
    run_id = uuid4()
    part_id = uuid4()
    async with session_factory() as session:
        async with session.begin():
            session.add(
                VideoModel(
                    id=video_id,
                    user_id=uuid4(),
                    title="transcription repository test",
                    category="test",
                    input_type="FILE",
                    storage_path="videos/source.mp4",
                    status="PROCESSING",
                )
            )
            session.add(
                PipelineRunModel(
                    id=run_id,
                    video_id=video_id,
                    pipeline_version="pipeline-v1",
                    normalization_status="COMPLETED",
                    normalization_completed=True,
                    total_part_count=1,
                )
            )
            session.add(
                PipelineAudioPartModel(
                    audio_part_id=part_id,
                    pipeline_run_id=run_id,
                    part_index=0,
                    start_ms=0,
                    end_ms=60_000,
                    audio_gcs_path="audio/part-000.flac",
                    stt_model_version="chirp_3",
                    status="RUNNING",
                    attempt_count=1,
                    message_id=41,
                    started_at=datetime.now(UTC),
                )
            )
    message = TranscribePartMessage(
        message_type=MessageType.TRANSCRIBE_PART,
        payload_version="v1",
        trace_id=uuid4(),
        attempt=1,
        pipeline_run_id=run_id,
        video_id=video_id,
        audio_part_id=part_id,
        part_index=0,
        stt_model_version="chirp_3",
        issued_at=datetime.now(UTC),
    )
    return message


def _artifact(message: TranscribePartMessage) -> TranscriptionArtifact:
    return TranscriptionArtifact.from_result(
        pipeline_run_id=message.pipeline_run_id,
        audio_part_id=message.audio_part_id,
        part_index=message.part_index,
        start_ms=0,
        end_ms=60_000,
        result=STTTranscriptionResult(
            stt_model_version=message.stt_model_version,
            segments=[TranscriptSegmentDTO("안녕.", 0, 500)],
            words=[TranscriptWordDTO("안녕.", 0, 500)],
        ),
    )


def _repository(session_factory, boundary) -> TranscriptionRepository:
    publisher = _Publisher()
    scheduler = PipelineWorkScheduler(
        SqlAlchemyPipelineDispatchUnitOfWork(session_factory, publisher)
    )
    return TranscriptionRepository(
        session_factory=session_factory,
        publisher=publisher,
        scheduler=scheduler,
        stt_capacity=2,
        assembly_boundary=boundary,
    )


@pytest.mark.asyncio
async def test_completion_and_assembly_boundary_commit_together(session_factory) -> None:
    message = await _seed_running_part(session_factory)
    boundary = _AssemblyBoundary()
    repository = _repository(session_factory, boundary)

    decision = await repository.complete(
        message,
        message_id=41,
        result_ref="results/part-000.json",
        artifact=_artifact(message),
    )

    async with session_factory() as session:
        part = await session.get(PipelineAudioPartModel, message.audio_part_id)
    assert decision.accepted is True
    assert part is not None
    assert part.status == "COMPLETED"
    assert part.result_ref == "results/part-000.json"
    assert boundary.seen_run_ids == [message.pipeline_run_id]


@pytest.mark.asyncio
async def test_boundary_failure_rolls_back_part_completion(session_factory) -> None:
    message = await _seed_running_part(session_factory)
    repository = _repository(session_factory, _AssemblyBoundary(fail=True))

    with pytest.raises(RuntimeError, match="assembly boundary failed"):
        await repository.complete(
            message,
            message_id=41,
            result_ref="results/part-000.json",
            artifact=_artifact(message),
        )

    async with session_factory() as session:
        part = await session.get(PipelineAudioPartModel, message.audio_part_id)
    assert part is not None
    assert part.status == "RUNNING"
    assert part.result_ref is None


@pytest.mark.asyncio
async def test_deleting_video_cancels_returned_part_result(session_factory) -> None:
    message = await _seed_running_part(session_factory)
    boundary = _AssemblyBoundary()
    repository = _repository(session_factory, boundary)
    async with session_factory() as session:
        async with session.begin():
            video = await session.get(VideoModel, message.video_id)
            assert video is not None
            video.status = "DELETING"

    decision = await repository.complete(
        message,
        message_id=41,
        result_ref="results/part-000.json",
        artifact=_artifact(message),
    )

    async with session_factory() as session:
        part = await session.get(PipelineAudioPartModel, message.audio_part_id)
    assert decision == type(decision)(False, "video_deleting")
    assert part is not None
    assert part.status == "CANCELLED"
    assert boundary.seen_run_ids == []


@pytest.mark.asyncio
async def test_out_of_order_completion_preserves_each_part_result(
    session_factory,
) -> None:
    first_message = await _seed_running_part(session_factory)
    second_part_id = uuid4()
    second_message = first_message.model_copy(
        update={"audio_part_id": second_part_id, "part_index": 1}
    )
    async with session_factory() as session:
        async with session.begin():
            run = await session.get(PipelineRunModel, first_message.pipeline_run_id)
            assert run is not None
            run.total_part_count = 2
            session.add(
                PipelineAudioPartModel(
                    audio_part_id=second_part_id,
                    pipeline_run_id=first_message.pipeline_run_id,
                    part_index=1,
                    start_ms=55_000,
                    end_ms=120_000,
                    audio_gcs_path="audio/part-001.flac",
                    stt_model_version="chirp_3",
                    status="RUNNING",
                    attempt_count=1,
                    message_id=42,
                    started_at=datetime.now(UTC),
                )
            )
    boundary = _AssemblyBoundary()
    repository = _repository(session_factory, boundary)

    second_decision = await repository.complete(
        second_message,
        message_id=42,
        result_ref="results/part-001.json",
        artifact=TranscriptionArtifact.from_result(
            pipeline_run_id=second_message.pipeline_run_id,
            audio_part_id=second_message.audio_part_id,
            part_index=1,
            start_ms=55_000,
            end_ms=120_000,
            result=STTTranscriptionResult(
                stt_model_version="chirp_3",
                segments=[TranscriptSegmentDTO("둘째.", 0, 500)],
                words=[TranscriptWordDTO("둘째.", 0, 500)],
            ),
        ),
    )
    first_decision = await repository.complete(
        first_message,
        message_id=41,
        result_ref="results/part-000.json",
        artifact=_artifact(first_message),
    )

    async with session_factory() as session:
        first_part = await session.get(
            PipelineAudioPartModel,
            first_message.audio_part_id,
        )
        second_part = await session.get(PipelineAudioPartModel, second_part_id)
    assert second_decision.accepted is True
    assert first_decision.accepted is True
    assert first_part is not None and first_part.result_ref == "results/part-000.json"
    assert second_part is not None and second_part.result_ref == "results/part-001.json"
    assert boundary.seen_run_ids == [
        first_message.pipeline_run_id,
        first_message.pipeline_run_id,
    ]


@pytest.mark.asyncio
async def test_terminal_failure_fails_part_run_and_video(session_factory) -> None:
    message = await _seed_running_part(session_factory)
    repository = _repository(session_factory, _AssemblyBoundary())

    failed = await repository.fail(
        message,
        message_id=41,
        failure_code="INVALID_REQUEST",
    )

    async with session_factory() as session:
        part = await session.get(PipelineAudioPartModel, message.audio_part_id)
        run = await session.get(PipelineRunModel, message.pipeline_run_id)
        video = await session.get(VideoModel, message.video_id)
    assert failed is True
    assert part is not None and part.status == "FAILED"
    assert run is not None and run.status == "FAILED" and not run.is_active
    assert video is not None and video.status == "FAILED"
    assert video.failed_stage == "TRANSCRIBE_PART"


@pytest.mark.asyncio
async def test_terminal_failure_rejects_mismatched_part_identity(session_factory) -> None:
    message = await _seed_running_part(session_factory)
    repository = _repository(session_factory, _AssemblyBoundary())
    mismatched_message = message.model_copy(update={"part_index": 1})

    failed = await repository.fail(
        mismatched_message,
        message_id=41,
        failure_code="INVALID_REQUEST",
    )

    async with session_factory() as session:
        part = await session.get(PipelineAudioPartModel, message.audio_part_id)
        run = await session.get(PipelineRunModel, message.pipeline_run_id)
        video = await session.get(VideoModel, message.video_id)
    assert failed is False
    assert part is not None and part.status == "RUNNING"
    assert run is not None and run.status != "FAILED"
    assert video is not None and video.status == "PROCESSING"
