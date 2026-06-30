"""add app user table

Revision ID: 0009_app_user
Revises: 0008_deploy_completed_run_status
Create Date: 2026-06-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009_app_user"
down_revision = "0008_deploy_completed_run_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default=sa.text("'USER'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('USER','ADMIN')", name="ck_app_user_role"),
        sa.CheckConstraint("status IN ('ACTIVE','DISABLED')", name="ck_app_user_status"),
    )
    op.create_index("uq_app_user_email", "app_user", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_app_user_email", table_name="app_user")
    op.drop_table("app_user")
