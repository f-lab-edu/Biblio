"""add pipeline worker artifact tables

Revision ID: 0002_pipeline_worker_artifacts
Revises: 0001_init_video
Create Date: 2026-03-17 02:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_pipeline_worker_artifacts"
down_revision = "0001_init_video"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("video.id"), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=True),
        sa.Column("end_ms", sa.Integer(), nullable=True),
    )

    op.create_table(
        "transcript_segment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("video.id"), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("stt_model_version", sa.String(length=64), nullable=False),
    )

    op.create_table(
        "chunk",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("video.id"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("enriched_text", sa.Text(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("keyframe_asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("asset.id"), nullable=True),
        sa.Column("chunking_version", sa.String(length=32), nullable=False),
        sa.Column("stt_model_version", sa.String(length=64), nullable=False),
        sa.Column("embedding_model_version", sa.String(length=64), nullable=False),
        sa.Column("visual_caption", sa.Text(), nullable=False, server_default=""),
        sa.Column("ocr_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("scene_tags", sa.Text(), nullable=False, server_default=""),
    )

    op.create_table(
        "vector_index_entry",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chunk.id"), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("video.id"), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("embedding_model_version", sa.String(length=64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("vector_index_entry")
    op.drop_table("chunk")
    op.drop_table("transcript_segment")
    op.drop_table("asset")
