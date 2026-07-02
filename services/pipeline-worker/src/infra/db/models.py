from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, SmallInteger, String, Text, Uuid, func
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
