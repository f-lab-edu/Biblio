"""add legacy reindex metadata schema

Revision ID: 0005_legacy_reindex_metadata
Revises: 0004_admin_ops_foundation
Create Date: 2026-05-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_legacy_reindex_metadata"
down_revision = "0004_admin_ops_foundation"
branch_labels = None
depends_on = None


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


def upgrade() -> None:
    op.create_table(
        "vector_index_catalog",
        sa.Column("index_name", sa.String(length=128), primary_key=True, nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retire_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_table(
        "legacy_reindex_item",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("video.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project.id"), nullable=True),
        sa.Column("source_index_name", sa.String(length=128), nullable=False),
        sa.Column("source_model_version", sa.String(length=128), nullable=False),
        sa.Column("target_index_name", sa.String(length=128), nullable=False),
        sa.Column("target_model_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("failed_stage", sa.Text(), nullable=True),
        sa.Column("failure_type", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completed_chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            _check_values("status", LEGACY_REINDEX_STATUSES),
            name="ck_legacy_reindex_item_status",
        ),
        sa.CheckConstraint(
            "failure_type IS NULL OR " + _check_values("failure_type", LEGACY_REINDEX_FAILURE_TYPES),
            name="ck_legacy_reindex_item_failure_type",
        ),
        sa.CheckConstraint(
            "failed_stage IS NULL OR " + _check_values("failed_stage", LEGACY_REINDEX_FAILED_STAGES),
            name="ck_legacy_reindex_item_failed_stage",
        ),
        sa.UniqueConstraint(
            "video_id",
            "source_index_name",
            "target_index_name",
            name="uq_legacy_reindex_item_video_source_target",
        ),
    )
    op.create_index(
        "idx_legacy_reindex_item_status_updated",
        "legacy_reindex_item",
        ["status", "updated_at"],
    )
    op.create_index(
        "idx_legacy_reindex_item_target_status",
        "legacy_reindex_item",
        ["target_index_name", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_legacy_reindex_item_target_status", table_name="legacy_reindex_item")
    op.drop_index("idx_legacy_reindex_item_status_updated", table_name="legacy_reindex_item")
    op.drop_table("legacy_reindex_item")
    op.drop_table("vector_index_catalog")
