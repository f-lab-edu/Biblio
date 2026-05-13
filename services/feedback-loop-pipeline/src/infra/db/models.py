from datetime import datetime
from uuid import UUID, uuid4

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
PROJECT_SERVING_STATES = ("SERVABLE", "ROLLBACK_EXCLUDED")
ML_RUN_STATUSES = ("PENDING", "RUNNING", "READY_FOR_RELEASE", "FAILED", "SUPERSEDED")
ML_FAILURE_TYPES = ("FAIL", "ERROR")
EVALUATION_STATUSES = ("RUNNING", "COMPLETED", "FAILED")
EVALUATION_DECISIONS = ("PASS", "FAIL")
RELEASE_STATUSES = ("STABLE", "CANDIDATE_REINDEXING", "ROLLBACK_PREPARING")


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
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    embedding_model_version: Mapped[str] = mapped_column(String(64), nullable=False)


class VectorIndexEntryModel(Base):
    __tablename__ = "vector_index_entry"

    index_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("chunk.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("project.id"), nullable=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"), nullable=False)
    embedding_model_version: Mapped[str] = mapped_column(String(64), nullable=False)
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
    rollback_snapshot_active_model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rollback_snapshot_active_index_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rollback_snapshot_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
