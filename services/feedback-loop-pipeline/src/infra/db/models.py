from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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


METADATA_JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")
VECTOR_COLUMN_TYPE = Vector().with_variant(JSON(), "sqlite")
PROJECT_SERVING_STATES = ("SERVABLE", "ROLLBACK_EXCLUDED")
ML_RUN_STATUSES = (
    "PENDING",
    "RUNNING",
    "READY_FOR_RELEASE",
    "DEPLOY_COMPLETED",
    "FAILED",
    "SUPERSEDED",
    "DEPLOYMENT_BLOCKED",
)
ML_FAILURE_TYPES = ("FAIL", "ERROR")
EVALUATION_STATUSES = ("RUNNING", "COMPLETED", "FAILED")
EVALUATION_DECISIONS = ("PASS", "FAIL")
RELEASE_STATUSES = ("STABLE", "CANDIDATE_REINDEXING", "ROLLBACK_PREPARING")
SNAPSHOT_STATUSES = ("ACTIVE", "PREVIOUS_STABLE", "ROLLED_BACK", "SUPERSEDED")
LEGACY_REINDEX_STATUSES = ("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED")
LEGACY_REINDEX_FAILURE_TYPES = ("FAIL", "ERROR")
LEGACY_REINDEX_FAILED_STAGES = (
    "TARGET_LOOKUP",
    "TEXT_LOAD",
    "EMBEDDING",
    "VECTOR_UPSERT",
    "CONSISTENCY_CHECK",
)


def _check_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted_values})"


class ProjectModel(Base):
    __tablename__ = "project"
    __table_args__ = (
        CheckConstraint(
            _check_values("search_serving_state", PROJECT_SERVING_STATES),
            name="ck_project_search_serving_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    search_serving_state: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        default="SERVABLE",
    )
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


class VideoModel(Base):
    __tablename__ = "video"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("project.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False, default="PENDING")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ChunkModel(Base):
    __tablename__ = "chunk"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"), nullable=False)
    chunk_index: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    enriched_text: Mapped[str | None] = mapped_column(Text(), nullable=True)
    embedding_model_version: Mapped[str] = mapped_column(String(64), nullable=False)


class VectorIndexEntryModel(Base):
    __tablename__ = "vector_index_entry"

    index_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("chunk.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("project.id"), nullable=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"), nullable=False)
    embedding_vector: Mapped[list[float] | None] = mapped_column(VECTOR_COLUMN_TYPE, nullable=True)
    embedding_model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class VectorIndexCatalogModel(Base):
    __tablename__ = "vector_index_catalog"

    index_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer(), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delete_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retire_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ModelEvaluationModel(Base):
    __tablename__ = "model_evaluation"
    __table_args__ = (
        CheckConstraint(_check_values("status", EVALUATION_STATUSES), name="ck_model_evaluation_status"),
        CheckConstraint(
            "overall_decision IS NULL OR " + _check_values("overall_decision", EVALUATION_DECISIONS),
            name="ck_model_evaluation_overall_decision",
        ),
        CheckConstraint("sample_count >= 0", name="ck_model_evaluation_sample_count"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    candidate_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    baseline_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluation_dataset_ref: Mapped[str] = mapped_column(Text(), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False)
    quality_metrics: Mapped[dict[str, float]] = mapped_column(
        METADATA_JSON_TYPE,
        nullable=False,
    )
    pass_criteria: Mapped[dict[str, object]] = mapped_column(
        METADATA_JSON_TYPE,
        nullable=False,
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
    __table_args__ = (
        CheckConstraint(_check_values("status", ML_RUN_STATUSES), name="ck_ml_pipeline_run_status"),
        CheckConstraint(
            "failure_type IS NULL OR " + _check_values("failure_type", ML_FAILURE_TYPES),
            name="ck_ml_pipeline_run_failure_type",
        ),
        Index(
            "uq_ml_pipeline_run_running",
            "status",
            unique=True,
            sqlite_where=text("status = 'RUNNING'"),
            postgresql_where=text("status = 'RUNNING'"),
        ),
        Index(
            "uq_ml_pipeline_run_pending",
            "status",
            unique=True,
            sqlite_where=text("status = 'PENDING'"),
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(Text(), nullable=False)
    failed_stage: Mapped[str | None] = mapped_column(Text(), nullable=True)
    failure_type: Mapped[str | None] = mapped_column(Text(), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    candidate_model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidate_index_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    baseline_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluation_id: Mapped[UUID | None] = mapped_column(ForeignKey("model_evaluation.id"), nullable=True)
    cutover_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deployment_attempt_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    last_deployment_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deployment_blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("ml_pipeline_run.id"), nullable=True)
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
    __table_args__ = (
        CheckConstraint("singleton_key = 1", name="ck_model_release_singleton_key"),
        CheckConstraint(_check_values("release_status", RELEASE_STATUSES), name="ck_model_release_status"),
        UniqueConstraint("singleton_key", name="uq_model_release_singleton_key"),
        Index("idx_model_release_status", "release_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
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


class ModelSnapshotModel(Base):
    __tablename__ = "model_snapshot"
    __table_args__ = (
        CheckConstraint(_check_values("status", SNAPSHOT_STATUSES), name="ck_model_snapshot_status"),
        Index(
            "uq_model_snapshot_active",
            "status",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "uq_model_snapshot_previous_stable",
            "status",
            unique=True,
            sqlite_where=text("status = 'PREVIOUS_STABLE'"),
            postgresql_where=text("status = 'PREVIOUS_STABLE'"),
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False)
    previous_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_snapshot.snapshot_id"), nullable=True
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LegacyReindexItemModel(Base):
    __tablename__ = "legacy_reindex_item"
    __table_args__ = (
        CheckConstraint(_check_values("status", LEGACY_REINDEX_STATUSES), name="ck_legacy_reindex_item_status"),
        CheckConstraint(
            "failure_type IS NULL OR " + _check_values("failure_type", LEGACY_REINDEX_FAILURE_TYPES),
            name="ck_legacy_reindex_item_failure_type",
        ),
        CheckConstraint(
            "failed_stage IS NULL OR " + _check_values("failed_stage", LEGACY_REINDEX_FAILED_STAGES),
            name="ck_legacy_reindex_item_failed_stage",
        ),
        UniqueConstraint(
            "video_id",
            "source_index_name",
            "target_index_name",
            name="uq_legacy_reindex_item_video_source_target",
        ),
        Index("idx_legacy_reindex_item_status_updated", "status", "updated_at"),
        Index("idx_legacy_reindex_item_target_status", "target_index_name", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
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
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
