"""add project lifecycle state

Revision ID: 0010_project_lifecycle_state
Revises: 0009_app_user
Create Date: 2026-06-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_project_lifecycle_state"
down_revision = "0009_app_user"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project",
        sa.Column(
            "lifecycle_state",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'ACTIVE'"),
        ),
    )
    op.create_check_constraint(
        "ck_project_lifecycle_state",
        "project",
        "lifecycle_state IN ('ACTIVE','DELETING')",
    )
    op.create_index("idx_project_lifecycle_state", "project", ["lifecycle_state"])


def downgrade() -> None:
    op.drop_index("idx_project_lifecycle_state", table_name="project")
    op.drop_constraint("ck_project_lifecycle_state", "project", type_="check")
    op.drop_column("project", "lifecycle_state")
