from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.video import Base


PROJECT_SERVING_STATES = ("SERVABLE", "ROLLBACK_EXCLUDED")
SNAPSHOT_STATUSES = ("ACTIVE", "PREVIOUS_STABLE", "ROLLED_BACK", "SUPERSEDED")
RELEASE_STATUSES = ("STABLE", "CANDIDATE_REINDEXING", "ROLLBACK_PREPARING")
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


class Project(Base):
    __tablename__ = "project"
    __table_args__ = (
        CheckConstraint(
            _check_values("search_serving_state", PROJECT_SERVING_STATES),
            name="ck_project_search_serving_state",
        ),
        Index("idx_project_user_created", "user_id", desc("created_at"), desc("id")),
        Index("idx_project_search_serving_state", "search_serving_state"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_serving_state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="SERVABLE",
        server_default=text("'SERVABLE'"),
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


class SearchResponseSnapshot(Base):
    __tablename__ = "search_response_snapshot"
    __table_args__ = (
        CheckConstraint(
            _check_values("project_serving_state", PROJECT_SERVING_STATES),
            name="ck_search_response_snapshot_project_serving_state",
        ),
        Index("idx_search_response_snapshot_user_req", "user_id", "req_id"),
        Index("idx_search_response_snapshot_expires_at", "expires_at"),
    )

    req_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("project.id"), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    topk_chunk_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    used_chunk_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    active_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    active_index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    served_vector_paths: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    project_serving_state: Mapped[str] = mapped_column(Text, nullable=False)
    scope_notice: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelEvaluation(Base):
    __tablename__ = "model_evaluation"
    __table_args__ = (
        CheckConstraint(_check_values("status", EVALUATION_STATUSES), name="ck_model_evaluation_status"),
        CheckConstraint(
            "overall_decision IS NULL OR " + _check_values("overall_decision", EVALUATION_DECISIONS),
            name="ck_model_evaluation_overall_decision",
        ),
        CheckConstraint("sample_count >= 0", name="ck_model_evaluation_sample_count"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    candidate_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    baseline_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluation_dataset_ref: Mapped[str] = mapped_column(Text, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    quality_metrics: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False)
    pass_criteria: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    overall_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    fail_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )


class MLPipelineRun(Base):
    __tablename__ = "ml_pipeline_run"
    __table_args__ = (
        CheckConstraint(_check_values("status", ML_RUN_STATUSES), name="ck_ml_pipeline_run_status"),
        CheckConstraint(
            "failure_type IS NULL OR " + _check_values("failure_type", ML_FAILURE_TYPES),
            name="ck_ml_pipeline_run_failure_type",
        ),
        Index("idx_ml_pipeline_run_status_created", "status", "created_at"),
        Index("uq_ml_pipeline_run_running", "status", unique=True, postgresql_where=text("status = 'RUNNING'")),
        Index("uq_ml_pipeline_run_pending", "status", unique=True, postgresql_where=text("status = 'PENDING'")),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    failed_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidate_index_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    baseline_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluation_id: Mapped[UUID | None] = mapped_column(ForeignKey("model_evaluation.id"), nullable=True)
    cutover_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deployment_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    last_deployment_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deployment_blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("ml_pipeline_run.id"), nullable=True)
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


class ModelRelease(Base):
    __tablename__ = "model_release"
    __table_args__ = (
        CheckConstraint("singleton_key = 1", name="ck_model_release_singleton_key"),
        CheckConstraint(
            _check_values("release_status", RELEASE_STATUSES),
            name="ck_model_release_status",
        ),
        UniqueConstraint("singleton_key", name="uq_model_release_singleton_key"),
        Index("idx_model_release_status", "release_status"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    singleton_key: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    release_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="STABLE",
        server_default=text("'STABLE'"),
    )
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


class ModelSnapshot(Base):
    __tablename__ = "model_snapshot"
    __table_args__ = (
        CheckConstraint(
            _check_values("status", SNAPSHOT_STATUSES),
            name="ck_model_snapshot_status",
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    previous_snapshot_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("model_snapshot.snapshot_id"),
        nullable=True,
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )


class VectorIndexCatalog(Base):
    __tablename__ = "vector_index_catalog"

    index_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delete_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retire_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )


class LegacyReindexItem(Base):
    __tablename__ = "legacy_reindex_item"
    __table_args__ = (
        CheckConstraint(
            _check_values("status", LEGACY_REINDEX_STATUSES),
            name="ck_legacy_reindex_item_status",
        ),
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

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("project.id"), nullable=True)
    source_index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    target_index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    target_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="PENDING",
        server_default=text("'PENDING'"),
    )
    failed_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    total_chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    completed_chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
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
        onupdate=func.now(),
    )
