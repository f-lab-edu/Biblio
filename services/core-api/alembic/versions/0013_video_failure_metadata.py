"""add video failure metadata

Revision ID: 0013_video_failure_metadata
Revises: 0012_processing_claimed_at
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0013_video_failure_metadata"
down_revision = "0012_processing_claimed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video",
        sa.Column("failure_code", sa.Text(), nullable=True),
    )
    op.add_column(
        "video",
        sa.Column(
            "failure_trace_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("video", "failure_trace_id")
    op.drop_column("video", "failure_code")
