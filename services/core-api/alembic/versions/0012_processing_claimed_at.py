"""add video processing claim timestamp

Revision ID: 0012_processing_claimed_at
Revises: 0011_search_conversation
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_processing_claimed_at"
down_revision = "0011_search_conversation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video",
        sa.Column(
            "processing_claimed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("video", "processing_claimed_at")
