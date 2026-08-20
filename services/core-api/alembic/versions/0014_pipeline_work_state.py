"""Add video pipeline work state tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_pipeline_work_state"
down_revision = "0013_video_failure_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORK_STATUSES = (
    "READY",
    "DISPATCHED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
)
ENRICHMENT_STATUSES = ("WAITING_FRAME", *WORK_STATUSES)
EMBEDDING_STATUSES = ("WAITING_ENRICHMENT", *WORK_STATUSES)
RUN_STATUSES = ("RUNNING", "COMPLETED", "FAILED", "SUPERSEDED", "CANCELLED")
PIPELINE_STAGES = (
    "NORMALIZE_VIDEO",
    "TRANSCRIBE_PART",
    "ASSEMBLE_CHUNKS",
    "ENRICH_CHUNK",
    "EMBED_BATCH",
)


def _check_values(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def _id_column(name: str = "id") -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    )


def _state_time_columns(prefix: str = "") -> list[sa.Column]:
    return [
        sa.Column(f"{prefix}ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(f"{prefix}dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(f"{prefix}started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(f"{prefix}completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(f"{prefix}failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(f"{prefix}cancelled_at", sa.DateTime(timezone=True), nullable=True),
    ]


def _audit_time_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    ]


def _create_pipeline_run() -> None:
    op.create_table(
        "pipeline_run",
        _id_column(),
        sa.Column(
            "video_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("video.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pipeline_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'RUNNING'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("normalization_status", sa.Text(), nullable=False, server_default=sa.text("'READY'")),
        sa.Column("normalization_attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("normalization_message_id", sa.BigInteger(), nullable=True),
        sa.Column("normalization_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("transcript_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("assembly_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("total_part_count", sa.Integer(), nullable=True),
        sa.Column("next_part_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_chunk_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pending_words", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("chunk_buffer", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("failure_code", sa.Text(), nullable=True),
        *_state_time_columns("normalization_"),
        *_audit_time_columns(),
        sa.CheckConstraint(_check_values("status", RUN_STATUSES), name="ck_pipeline_run_status"),
        sa.CheckConstraint("NOT is_active OR status = 'RUNNING'", name="ck_pipeline_run_active_status"),
        sa.CheckConstraint(_check_values("normalization_status", WORK_STATUSES), name="ck_pipeline_run_normalization_status"),
        sa.CheckConstraint("total_part_count IS NULL OR total_part_count >= 0", name="ck_pipeline_run_total_part_count"),
        sa.CheckConstraint("next_part_index >= 0", name="ck_pipeline_run_next_part_index"),
        sa.CheckConstraint("next_chunk_index >= 0", name="ck_pipeline_run_next_chunk_index"),
    )
    op.create_index("idx_pipeline_run_video_created", "pipeline_run", ["video_id", "created_at"])
    op.create_index(
        "uq_pipeline_run_active_video",
        "pipeline_run",
        ["video_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def _create_pipeline_audio_part() -> None:
    op.create_table(
        "pipeline_audio_part",
        _id_column("audio_part_id"),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pipeline_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("part_index", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("audio_gcs_path", sa.Text(), nullable=False),
        sa.Column("stt_model_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'READY'")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("result_ref", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.Text(), nullable=True),
        *_state_time_columns(),
        *_audit_time_columns(),
        sa.CheckConstraint(_check_values("status", WORK_STATUSES), name="ck_pipeline_audio_part_status"),
        sa.CheckConstraint("part_index >= 0", name="ck_pipeline_audio_part_index"),
        sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_pipeline_audio_part_time_range"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_pipeline_audio_part_attempt_count"),
        sa.UniqueConstraint("pipeline_run_id", "part_index", name="uq_pipeline_audio_part_run_index"),
    )
    op.create_index("idx_pipeline_audio_part_status_ready", "pipeline_audio_part", ["status", "ready_at"])


def _create_pipeline_embedding_batch() -> None:
    op.create_table(
        "pipeline_embedding_batch",
        _id_column("batch_id"),
        sa.Column("chunk_work_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("embedding_model_version", sa.String(length=64), nullable=False),
        sa.Column("index_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'DISPATCHED'")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("failure_code", sa.Text(), nullable=True),
        *_state_time_columns(),
        *_audit_time_columns(),
        sa.CheckConstraint(_check_values("status", WORK_STATUSES), name="ck_pipeline_embedding_batch_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_pipeline_embedding_batch_attempt_count"),
    )
    op.create_index("idx_pipeline_embedding_batch_status", "pipeline_embedding_batch", ["status", "dispatched_at"])


def _create_pipeline_chunk_work() -> None:
    op.create_table(
        "pipeline_chunk_work",
        _id_column("chunk_work_id"),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pipeline_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("frame_ref", sa.Text(), nullable=True),
        sa.Column("chunking_version", sa.String(length=32), nullable=False),
        sa.Column("stt_model_version", sa.String(length=64), nullable=False),
        sa.Column("embedding_model_version", sa.String(length=64), nullable=False),
        sa.Column("index_name", sa.String(length=128), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chunk.id"), nullable=True),
        sa.Column("enrichment_status", sa.Text(), nullable=False),
        sa.Column("enrichment_attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("enrichment_message_id", sa.BigInteger(), nullable=True),
        sa.Column("enrichment_failure_code", sa.Text(), nullable=True),
        sa.Column("embedding_status", sa.Text(), nullable=False, server_default=sa.text("'WAITING_ENRICHMENT'")),
        sa.Column("embedding_attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("embedding_batch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pipeline_embedding_batch.batch_id", ondelete="SET NULL"), nullable=True),
        sa.Column("embedding_failure_code", sa.Text(), nullable=True),
        *_state_time_columns("enrichment_"),
        *_state_time_columns("embedding_"),
        *_audit_time_columns(),
        sa.CheckConstraint(_check_values("enrichment_status", ENRICHMENT_STATUSES), name="ck_pipeline_chunk_work_enrichment_status"),
        sa.CheckConstraint(_check_values("embedding_status", EMBEDDING_STATUSES), name="ck_pipeline_chunk_work_embedding_status"),
        sa.CheckConstraint("chunk_index >= 0", name="ck_pipeline_chunk_work_index"),
        sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_pipeline_chunk_work_time_range"),
        sa.CheckConstraint("enrichment_attempt_count >= 0", name="ck_pipeline_chunk_work_enrichment_attempt_count"),
        sa.CheckConstraint("embedding_attempt_count >= 0", name="ck_pipeline_chunk_work_embedding_attempt_count"),
        sa.UniqueConstraint("pipeline_run_id", "chunk_index", name="uq_pipeline_chunk_work_run_index"),
    )
    op.create_index("idx_pipeline_chunk_work_enrichment", "pipeline_chunk_work", ["enrichment_status", "enrichment_ready_at"])
    op.create_index("idx_pipeline_chunk_work_embedding", "pipeline_chunk_work", ["embedding_status", "embedding_ready_at"])


def _create_pipeline_stage_schedule() -> None:
    op.create_table(
        "pipeline_stage_schedule",
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pipeline_run.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("stage", sa.Text(), primary_key=True, nullable=False),
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_time_columns(),
        sa.CheckConstraint(_check_values("stage", PIPELINE_STAGES), name="ck_pipeline_stage_schedule_stage"),
    )


def upgrade() -> None:
    _create_pipeline_run()
    _create_pipeline_audio_part()
    _create_pipeline_embedding_batch()
    _create_pipeline_chunk_work()
    _create_pipeline_stage_schedule()


def downgrade() -> None:
    op.drop_table("pipeline_stage_schedule")
    op.drop_table("pipeline_chunk_work")
    op.drop_table("pipeline_embedding_batch")
    op.drop_table("pipeline_audio_part")
    op.drop_table("pipeline_run")
