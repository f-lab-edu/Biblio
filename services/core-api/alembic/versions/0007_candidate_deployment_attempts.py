"""add candidate deployment attempt tracking

Revision ID: 0007_candidate_deploy_attempts
Revises: 0006_model_snapshot_registry
Create Date: 2026-06-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_candidate_deploy_attempts"
down_revision = "0006_model_snapshot_registry"
branch_labels = None
depends_on = None


ML_RUN_STATUSES = (
    "PENDING",
    "RUNNING",
    "READY_FOR_RELEASE",
    "FAILED",
    "SUPERSEDED",
    "DEPLOYMENT_BLOCKED",
)
OLD_ML_RUN_STATUSES = ("PENDING", "RUNNING", "READY_FOR_RELEASE", "FAILED", "SUPERSEDED")


def _check_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted_values})"


def upgrade() -> None:
    op.drop_constraint("ck_ml_pipeline_run_status", "ml_pipeline_run", type_="check")
    op.add_column(
        "ml_pipeline_run",
        sa.Column(
            "deployment_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "ml_pipeline_run",
        sa.Column("last_deployment_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ml_pipeline_run",
        sa.Column("deployment_blocked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_ml_pipeline_run_status",
        "ml_pipeline_run",
        _check_values("status", ML_RUN_STATUSES),
    )


def downgrade() -> None:
    op.drop_constraint("ck_ml_pipeline_run_status", "ml_pipeline_run", type_="check")
    op.drop_column("ml_pipeline_run", "deployment_blocked_at")
    op.drop_column("ml_pipeline_run", "last_deployment_attempt_at")
    op.drop_column("ml_pipeline_run", "deployment_attempt_count")
    op.create_check_constraint(
        "ck_ml_pipeline_run_status",
        "ml_pipeline_run",
        _check_values("status", OLD_ML_RUN_STATUSES),
    )
