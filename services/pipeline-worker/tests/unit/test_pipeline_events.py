from uuid import uuid4

from loguru import logger

from src.schemas.messages import MessageType, TranscribePartMessage
from src.telemetry.pipeline_events import (
    emit_pipeline_work_event,
    work_log_context_from_message,
)


def test_transcription_context_uses_audio_part_as_work_id() -> None:
    run_id = uuid4()
    video_id = uuid4()
    part_id = uuid4()
    message = TranscribePartMessage.model_validate(
        {
            "message_type": MessageType.TRANSCRIBE_PART.value,
            "payload_version": "v1",
            "trace_id": str(uuid4()),
            "attempt": 2,
            "pipeline_run_id": str(run_id),
            "video_id": str(video_id),
            "audio_part_id": str(part_id),
            "part_index": 3,
            "stt_model_version": "chirp_3",
            "issued_at": "2026-08-23T00:00:00Z",
        }
    )

    context = work_log_context_from_message(
        message,
        message_id=41,
        read_ct=2,
    )

    assert context.stage == "TRANSCRIBE_PART"
    assert context.work_id == str(part_id)
    assert context.pipeline_run_id == str(run_id)
    assert context.part_index == 3
    assert context.work_attempt == 2
    assert context.message_id == 41
    assert context.read_ct == 2


def test_pipeline_work_event_binds_schema_and_lifecycle_fields() -> None:
    message = TranscribePartMessage.model_validate(
        {
            "message_type": MessageType.TRANSCRIBE_PART.value,
            "payload_version": "v1",
            "trace_id": str(uuid4()),
            "attempt": 1,
            "pipeline_run_id": str(uuid4()),
            "video_id": str(uuid4()),
            "audio_part_id": str(uuid4()),
            "part_index": 0,
            "stt_model_version": "chirp_3",
            "issued_at": "2026-08-23T00:00:00Z",
        }
    )
    records: list[dict] = []
    sink_id = logger.add(lambda log_message: records.append(log_message.record))
    try:
        emit_pipeline_work_event(
            "pipeline.work.started",
            work_log_context_from_message(message, message_id=7, read_ct=1),
            queue_wait_ms=125.5,
        )
    finally:
        logger.remove(sink_id)

    record = records[0]
    assert record["message"] == "pipeline.work.started"
    assert record["extra"]["log_schema_version"] == 2
    assert record["extra"]["event_name"] == "pipeline.work.started"
    assert record["extra"]["queue_wait_ms"] == 125.5
