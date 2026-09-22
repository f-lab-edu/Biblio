from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


VECTOR_COLUMN_TYPE = Vector().with_variant(JSON(), "sqlite")
METADATA_JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class ProjectModel(Base):
    __tablename__ = "project"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    search_serving_state: Mapped[str] = mapped_column(
        Text(), nullable=False, default="SERVABLE"
    )
    lifecycle_state: Mapped[str] = mapped_column(
        Text(), nullable=False, default="ACTIVE"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )


class VideoModel(Base):
    __tablename__ = "video"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(Text(), nullable=False)
    input_type: Mapped[str] = mapped_column(Text(), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(Text(), nullable=True)
    status: Mapped[str] = mapped_column(Text(), nullable=False, default="PENDING")
    failed_stage: Mapped[str | None] = mapped_column(Text(), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(Text(), nullable=True)
    failure_trace_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    processing_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )


class PipelineRunModel(Base):
    __tablename__ = "pipeline_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING','COMPLETED','FAILED','SUPERSEDED','CANCELLED')",
            name="ck_pipeline_run_status",
        ),
        CheckConstraint(
            "normalization_status IN ('READY','DISPATCHED','RUNNING','COMPLETED','FAILED','CANCELLED')",
            name="ck_pipeline_run_normalization_status",
        ),
        CheckConstraint(
            "NOT is_active OR status = 'RUNNING'",
            name="ck_pipeline_run_active_status",
        ),
        CheckConstraint(
            "total_part_count IS NULL OR total_part_count >= 0",
            name="ck_pipeline_run_total_part_count",
        ),
        CheckConstraint("next_part_index >= 0", name="ck_pipeline_run_next_part_index"),
        CheckConstraint("next_chunk_index >= 0", name="ck_pipeline_run_next_chunk_index"),
        Index("idx_pipeline_run_video_created", "video_id", "created_at"),
        Index(
            "uq_pipeline_run_active_video",
            "video_id",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id", ondelete="CASCADE"), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False, default="RUNNING")
    is_active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)
    normalization_status: Mapped[str] = mapped_column(Text(), nullable=False, default="READY")
    normalization_attempt_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    normalization_message_id: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    source_storage_path: Mapped[str | None] = mapped_column(Text(), nullable=True)
    source_generation: Mapped[str | None] = mapped_column(Text(), nullable=True)
    normalization_completed: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    transcript_completed: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    assembly_completed: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    total_part_count: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    next_part_index: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    next_chunk_index: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    pending_words: Mapped[list[dict[str, object]]] = mapped_column(METADATA_JSON_TYPE, nullable=False, default=list)
    chunk_buffer: Mapped[list[dict[str, object]]] = mapped_column(METADATA_JSON_TYPE, nullable=False, default=list)
    failure_code: Mapped[str | None] = mapped_column(Text(), nullable=True)
    normalization_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    normalization_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    normalization_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    normalization_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    normalization_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    normalization_cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now(), onupdate=func.now())


class PipelineAudioPartModel(Base):
    __tablename__ = "pipeline_audio_part"
    __table_args__ = (
        CheckConstraint(
            "status IN ('READY','DISPATCHED','RUNNING','COMPLETED','FAILED','CANCELLED')",
            name="ck_pipeline_audio_part_status",
        ),
        CheckConstraint("part_index >= 0", name="ck_pipeline_audio_part_index"),
        CheckConstraint(
            "start_ms >= 0 AND end_ms > start_ms",
            name="ck_pipeline_audio_part_time_range",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_pipeline_audio_part_attempt_count"),
        UniqueConstraint(
            "pipeline_run_id",
            "part_index",
            name="uq_pipeline_audio_part_run_index",
        ),
        Index("idx_pipeline_audio_part_status_ready", "status", "ready_at"),
    )

    audio_part_id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    pipeline_run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_run.id", ondelete="CASCADE"), nullable=False)
    part_index: Mapped[int] = mapped_column(Integer(), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer(), nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer(), nullable=False)
    audio_gcs_path: Mapped[str] = mapped_column(Text(), nullable=False)
    stt_model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False, default="READY")
    attempt_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    message_id: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    result_ref: Mapped[str | None] = mapped_column(Text(), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(Text(), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now(), onupdate=func.now())


class PipelineFrameCandidateModel(Base):
    __tablename__ = "pipeline_frame_candidate"
    __table_args__ = (
        CheckConstraint(
            "frame_index >= 0",
            name="ck_pipeline_frame_candidate_index",
        ),
        CheckConstraint(
            "timestamp_ms >= 0",
            name="ck_pipeline_frame_candidate_timestamp",
        ),
        UniqueConstraint(
            "pipeline_run_id",
            "frame_index",
            name="uq_pipeline_frame_candidate_run_index",
        ),
        UniqueConstraint(
            "pipeline_run_id",
            "timestamp_ms",
            name="uq_pipeline_frame_candidate_run_timestamp",
        ),
        Index(
            "idx_pipeline_frame_candidate_run_timestamp",
            "pipeline_run_id",
            "timestamp_ms",
        ),
    )

    frame_candidate_id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    pipeline_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("pipeline_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    frame_index: Mapped[int] = mapped_column(Integer(), nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(Integer(), nullable=False)
    frame_gcs_path: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )


class PipelineEmbeddingBatchModel(Base):
    __tablename__ = "pipeline_embedding_batch"
    __table_args__ = (
        CheckConstraint(
            "status IN ('READY','DISPATCHED','RUNNING','COMPLETED','FAILED','CANCELLED')",
            name="ck_pipeline_embedding_batch_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_pipeline_embedding_batch_attempt_count",
        ),
        Index(
            "idx_pipeline_embedding_batch_status",
            "status",
            "dispatched_at",
        ),
    )

    batch_id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    chunk_work_ids: Mapped[list[str]] = mapped_column(METADATA_JSON_TYPE, nullable=False)
    embedding_model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False, default="DISPATCHED")
    attempt_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    message_id: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(Text(), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now(), onupdate=func.now())


class PipelineChunkWorkModel(Base):
    __tablename__ = "pipeline_chunk_work"
    __table_args__ = (
        CheckConstraint(
            "enrichment_status IN ('WAITING_FRAME','READY','DISPATCHED','RUNNING','COMPLETED','FAILED','CANCELLED')",
            name="ck_pipeline_chunk_work_enrichment_status",
        ),
        CheckConstraint(
            "embedding_status IN ('WAITING_ENRICHMENT','READY','DISPATCHED','RUNNING','COMPLETED','FAILED','CANCELLED')",
            name="ck_pipeline_chunk_work_embedding_status",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_pipeline_chunk_work_index"),
        CheckConstraint(
            "start_ms >= 0 AND end_ms > start_ms",
            name="ck_pipeline_chunk_work_time_range",
        ),
        CheckConstraint(
            "enrichment_attempt_count >= 0",
            name="ck_pipeline_chunk_work_enrichment_attempt_count",
        ),
        CheckConstraint(
            "embedding_attempt_count >= 0",
            name="ck_pipeline_chunk_work_embedding_attempt_count",
        ),
        UniqueConstraint(
            "pipeline_run_id",
            "chunk_index",
            name="uq_pipeline_chunk_work_run_index",
        ),
        Index(
            "idx_pipeline_chunk_work_enrichment",
            "enrichment_status",
            "enrichment_ready_at",
        ),
        Index(
            "idx_pipeline_chunk_work_embedding",
            "embedding_status",
            "embedding_ready_at",
        ),
    )

    chunk_work_id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    pipeline_run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_run.id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer(), nullable=False)
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer(), nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer(), nullable=False)
    frame_ref: Mapped[str | None] = mapped_column(Text(), nullable=True)
    chunking_version: Mapped[str] = mapped_column(String(32), nullable=False)
    stt_model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    chunk_id: Mapped[UUID | None] = mapped_column(ForeignKey("chunk.id"), nullable=True)
    enrichment_status: Mapped[str] = mapped_column(Text(), nullable=False)
    enrichment_attempt_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    enrichment_message_id: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    enrichment_failure_code: Mapped[str | None] = mapped_column(Text(), nullable=True)
    embedding_status: Mapped[str] = mapped_column(Text(), nullable=False, default="WAITING_ENRICHMENT")
    embedding_attempt_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    embedding_batch_id: Mapped[UUID | None] = mapped_column(ForeignKey("pipeline_embedding_batch.batch_id", ondelete="SET NULL"), nullable=True)
    embedding_failure_code: Mapped[str | None] = mapped_column(Text(), nullable=True)
    enrichment_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrichment_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrichment_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrichment_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrichment_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrichment_cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding_cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now(), onupdate=func.now())


class PipelineStageScheduleModel(Base):
    __tablename__ = "pipeline_stage_schedule"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('NORMALIZE_VIDEO','TRANSCRIBE_PART','ASSEMBLE_CHUNKS','ENRICH_CHUNK','EMBED_BATCH')",
            name="ck_pipeline_stage_schedule_stage",
        ),
    )

    pipeline_run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_run.id", ondelete="CASCADE"), primary_key=True)
    stage: Mapped[str] = mapped_column(Text(), primary_key=True)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now(), onupdate=func.now())


class AssetModel(Base):
    __tablename__ = "asset"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"))
    asset_type: Mapped[str] = mapped_column(String(32))
    storage_path: Mapped[str] = mapped_column(Text())
    start_ms: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer(), nullable=True)


class TranscriptSegmentModel(Base):
    __tablename__ = "transcript_segment"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"))
    segment_index: Mapped[int] = mapped_column(Integer())
    text: Mapped[str] = mapped_column(Text())
    start_ms: Mapped[int] = mapped_column(Integer())
    end_ms: Mapped[int] = mapped_column(Integer())
    stt_model_version: Mapped[str] = mapped_column(String(64))


class ChunkModel(Base):
    __tablename__ = "chunk"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"))
    chunk_index: Mapped[int] = mapped_column(Integer())
    text: Mapped[str] = mapped_column(Text())
    enriched_text: Mapped[str] = mapped_column(Text())
    start_ms: Mapped[int] = mapped_column(Integer())
    end_ms: Mapped[int] = mapped_column(Integer())
    keyframe_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("asset.id"), nullable=True)
    chunking_version: Mapped[str] = mapped_column(String(32))
    stt_model_version: Mapped[str] = mapped_column(String(64))
    embedding_model_version: Mapped[str] = mapped_column(String(64))
    visual_caption: Mapped[str] = mapped_column(Text(), default="")
    ocr_text: Mapped[str] = mapped_column(Text(), default="")
    scene_tags: Mapped[str] = mapped_column(Text(), default="")


class VectorIndexEntryModel(Base):
    __tablename__ = "vector_index_entry"

    index_name: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
        default="default-index",
    )
    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("chunk.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project.id"), nullable=True
    )
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"))
    embedding_vector: Mapped[list[float]] = mapped_column(VECTOR_COLUMN_TYPE, nullable=False)
    embedding_model_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )


class SearchResponseSnapshotModel(Base):
    __tablename__ = "search_response_snapshot"

    req_id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("project.id"), nullable=False)
    query_text: Mapped[str] = mapped_column(Text(), nullable=False)
    topk_chunk_ids: Mapped[list[str]] = mapped_column(METADATA_JSON_TYPE, nullable=False)
    used_chunk_ids: Mapped[list[str]] = mapped_column(METADATA_JSON_TYPE, nullable=False)
    active_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    active_index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    served_vector_paths: Mapped[list[dict[str, str]]] = mapped_column(METADATA_JSON_TYPE, nullable=False)
    project_serving_state: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SearchConversationModel(Base):
    __tablename__ = "search_conversation"

    req_id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("project.id"), nullable=False)
    query: Mapped[str] = mapped_column(Text(), nullable=False)
    answer: Mapped[str] = mapped_column(Text(), nullable=False)
    sources: Mapped[list[dict[str, object]]] = mapped_column(
        METADATA_JSON_TYPE, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )


class LegacyReindexItemModel(Base):
    __tablename__ = "legacy_reindex_item"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("project.id"), nullable=True)
    source_index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    target_index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    target_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False, default="PENDING")
    failed_stage: Mapped[str | None] = mapped_column(Text(), nullable=True)
    failure_type: Mapped[str | None] = mapped_column(Text(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text(), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    total_chunk_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    completed_chunk_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )


class ModelEvaluationModel(Base):
    __tablename__ = "model_evaluation"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    candidate_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    baseline_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluation_dataset_ref: Mapped[str] = mapped_column(Text(), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False)
    quality_metrics: Mapped[dict[str, float]] = mapped_column(
        METADATA_JSON_TYPE, nullable=False
    )
    pass_criteria: Mapped[dict[str, object]] = mapped_column(
        METADATA_JSON_TYPE, nullable=False
    )
    overall_decision: Mapped[str | None] = mapped_column(Text(), nullable=True)
    fail_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MLPipelineRunModel(Base):
    __tablename__ = "ml_pipeline_run"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    status: Mapped[str] = mapped_column(Text(), nullable=False)
    failed_stage: Mapped[str | None] = mapped_column(Text(), nullable=True)
    failure_type: Mapped[str | None] = mapped_column(Text(), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    candidate_model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidate_index_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    baseline_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_evaluation.id"), nullable=True
    )
    cutover_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_run_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ModelReleaseModel(Base):
    __tablename__ = "model_release"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    singleton_key: Mapped[int] = mapped_column(SmallInteger(), nullable=False, default=1)
    release_status: Mapped[str] = mapped_column(Text(), nullable=False, default="STABLE")
    active_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    active_index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    previous_model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    previous_index_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidate_model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidate_index_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidate_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    candidate_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    switched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
