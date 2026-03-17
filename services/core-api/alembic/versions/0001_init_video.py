"""init video table

Revision ID: 0001_init_video
Revises:
Create Date: 2026-03-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_init_video"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.create_table(
        "video",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("input_type", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("failed_stage", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "category IN ('GENERAL','IT','MEDICAL','LEGAL')",
            name="ck_video_category",
        ),
        sa.CheckConstraint(
            "input_type IN ('LOCAL_FILE','EXTERNAL_URL')",
            name="ck_video_input_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','UPLOADED','PROCESSING','READY','FAILED','DELETING')",
            name="ck_video_status",
        ),
        sa.CheckConstraint(
            "failed_stage IS NULL OR failed_stage IN ('DOWNLOAD','EXTRACT','STT','CHUNKING','EMBEDDING','VECTOR_UPSERT')",
            name="ck_video_failed_stage",
        ),
    )
    op.execute("CREATE INDEX idx_video_user_created ON video(user_id, created_at DESC, id DESC)")
    op.create_index("idx_video_user_status", "video", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("idx_video_user_status", table_name="video")
    op.execute("DROP INDEX IF EXISTS idx_video_user_created")
    op.drop_table("video")
