"""Add run-scoped normalization frame candidates."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_pipeline_frame_candidate"
down_revision = "0014_pipeline_work_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pipeline_run",
        sa.Column("source_storage_path", sa.Text(), nullable=True),
    )
    op.add_column(
        "pipeline_run",
        sa.Column("source_generation", sa.Text(), nullable=True),
    )
    op.create_table(
        "pipeline_frame_candidate",
        sa.Column(
            "frame_candidate_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipeline_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("frame_index", sa.Integer(), nullable=False),
        sa.Column("timestamp_ms", sa.Integer(), nullable=False),
        sa.Column("frame_gcs_path", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "frame_index >= 0",
            name="ck_pipeline_frame_candidate_index",
        ),
        sa.CheckConstraint(
            "timestamp_ms >= 0",
            name="ck_pipeline_frame_candidate_timestamp",
        ),
        sa.UniqueConstraint(
            "pipeline_run_id",
            "frame_index",
            name="uq_pipeline_frame_candidate_run_index",
        ),
        sa.UniqueConstraint(
            "pipeline_run_id",
            "timestamp_ms",
            name="uq_pipeline_frame_candidate_run_timestamp",
        ),
    )
    op.create_index(
        "idx_pipeline_frame_candidate_run_timestamp",
        "pipeline_frame_candidate",
        ["pipeline_run_id", "timestamp_ms"],
    )


def downgrade() -> None:
    op.drop_table("pipeline_frame_candidate")
    op.drop_column("pipeline_run", "source_generation")
    op.drop_column("pipeline_run", "source_storage_path")
