from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from src.schemas.messages import (
    EmbedBatchMessage,
    EnrichChunkMessage,
    NormalizeVideoMessage,
    StageMessage,
    TranscribePartMessage,
)
from src.utils.logging import get_logger

LOG_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class PipelineWorkLogContext:
    trace_id: str
    video_id: str
    pipeline_run_id: str
    stage: str
    work_id: str
    work_attempt: int
    part_index: int | None = None
    chunk_index: int | None = None
    batch_id: str | None = None
    queue_name: str | None = None
    message_id: int | None = None
    read_ct: int | None = None

    def fields(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }


def work_log_context_from_message(
    message: StageMessage,
    *,
    message_id: int | None = None,
    read_ct: int | None = None,
) -> PipelineWorkLogContext:
    common = {
        "trace_id": str(message.trace_id),
        "pipeline_run_id": (
            str(message.pipeline_run_id)
            if message.pipeline_run_id is not None
            else "-"
        ),
        "stage": message.message_type.value,
        "work_attempt": message.attempt,
        "queue_name": message.message_type.value,
        "message_id": message_id,
        "read_ct": read_ct,
    }
    if isinstance(message, NormalizeVideoMessage):
        return PipelineWorkLogContext(
            **common,
            video_id=str(message.video_id),
            work_id=str(message.pipeline_run_id),
        )
    if isinstance(message, TranscribePartMessage):
        return PipelineWorkLogContext(
            **common,
            video_id=str(message.video_id),
            work_id=str(message.audio_part_id),
            part_index=message.part_index,
        )
    if isinstance(message, EnrichChunkMessage):
        return PipelineWorkLogContext(
            **common,
            video_id=str(message.video_id),
            work_id=str(message.chunk_work_id),
            chunk_index=message.chunk_index,
        )
    if isinstance(message, EmbedBatchMessage):
        return PipelineWorkLogContext(
            **common,
            video_id="-",
            work_id=str(message.batch_id),
            batch_id=str(message.batch_id),
        )
    raise TypeError(f"Unsupported stage message: {type(message).__name__}")


def emit_pipeline_work_event(
    event_name: str,
    context: PipelineWorkLogContext,
    *,
    timestamp_utc: datetime | None = None,
    level: str = "INFO",
    **fields: object,
) -> None:
    event_timestamp = timestamp_utc or datetime.now(UTC)
    get_logger().bind(
        log_schema_version=LOG_SCHEMA_VERSION,
        timestamp_utc=event_timestamp.astimezone(UTC).isoformat(),
        event_name=event_name,
        **context.fields(),
        **fields,
    ).log(level, event_name)
