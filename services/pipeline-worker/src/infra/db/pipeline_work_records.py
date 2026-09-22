from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

WorkStatus = Literal[
    "READY", "DISPATCHED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"
]
RunStatus = Literal["RUNNING", "COMPLETED", "FAILED", "SUPERSEDED", "CANCELLED"]
EnrichmentStatus = Literal[
    "WAITING_FRAME",
    "READY",
    "DISPATCHED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
]
EmbeddingStatus = Literal[
    "WAITING_ENRICHMENT",
    "READY",
    "DISPATCHED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
]
PipelineStage = Literal[
    "NORMALIZE_VIDEO",
    "TRANSCRIBE_PART",
    "ASSEMBLE_CHUNKS",
    "ENRICH_CHUNK",
    "EMBED_BATCH",
]


@dataclass(frozen=True, slots=True)
class WorkTimestamps:
    ready_at: datetime | None
    dispatched_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    cancelled_at: datetime | None


@dataclass(frozen=True, slots=True)
class PipelineRunRecord:
    id: UUID
    video_id: UUID
    pipeline_version: str
    status: RunStatus
    is_active: bool
    normalization_status: WorkStatus
    normalization_attempt_count: int
    normalization_message_id: int | None
    normalization_completed: bool
    transcript_completed: bool
    assembly_completed: bool
    total_part_count: int | None
    next_part_index: int
    next_chunk_index: int
    pending_words: list[dict[str, object]]
    chunk_buffer: list[dict[str, object]]
    failure_code: str | None
    normalization_timestamps: WorkTimestamps
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PipelineAudioPartRecord:
    audio_part_id: UUID
    pipeline_run_id: UUID
    part_index: int
    start_ms: int
    end_ms: int
    audio_gcs_path: str
    stt_model_version: str
    status: WorkStatus
    attempt_count: int
    message_id: int | None
    result_ref: str | None
    failure_code: str | None
    timestamps: WorkTimestamps
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PipelineChunkWorkRecord:
    chunk_work_id: UUID
    pipeline_run_id: UUID
    chunk_index: int
    text: str
    start_ms: int
    end_ms: int
    frame_ref: str | None
    chunking_version: str
    stt_model_version: str
    embedding_model_version: str
    index_name: str
    chunk_id: UUID | None
    enrichment_status: EnrichmentStatus
    enrichment_attempt_count: int
    enrichment_message_id: int | None
    enrichment_failure_code: str | None
    embedding_status: EmbeddingStatus
    embedding_attempt_count: int
    embedding_batch_id: UUID | None
    embedding_failure_code: str | None
    enrichment_timestamps: WorkTimestamps
    embedding_timestamps: WorkTimestamps
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PipelineEmbeddingBatchRecord:
    batch_id: UUID
    chunk_work_ids: list[UUID]
    embedding_model_version: str
    index_name: str
    status: WorkStatus
    attempt_count: int
    message_id: int | None
    failure_code: str | None
    timestamps: WorkTimestamps
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PipelineStageScheduleRecord:
    pipeline_run_id: UUID
    stage: PipelineStage
    last_dispatched_at: datetime | None
    created_at: datetime
    updated_at: datetime
