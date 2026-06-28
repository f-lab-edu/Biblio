"""add deploy completed ml run status

Revision ID: 0008_deploy_completed_run_status
Revises: 0007_candidate_deploy_attempts
Create Date: 2026-06-28
"""

from __future__ import annotations

from alembic import op


revision = "0008_deploy_completed_run_status"
down_revision = "0007_candidate_deploy_attempts"
branch_labels = None
depends_on = None


ML_RUN_STATUSES = (
    "PENDING",
    "RUNNING",
    "READY_FOR_RELEASE",
    "DEPLOY_COMPLETED",
    "FAILED",
    "SUPERSEDED",
    "DEPLOYMENT_BLOCKED",
)
OLD_ML_RUN_STATUSES = (
    "PENDING",
    "RUNNING",
    "READY_FOR_RELEASE",
    "FAILED",
    "SUPERSEDED",
    "DEPLOYMENT_BLOCKED",
)


def _check_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted_values})"


def upgrade() -> None:
    op.drop_constraint("ck_ml_pipeline_run_status", "ml_pipeline_run", type_="check")
    op.create_check_constraint(
        "ck_ml_pipeline_run_status",
        "ml_pipeline_run",
        _check_values("status", ML_RUN_STATUSES),
    )


def downgrade() -> None:
    op.drop_constraint("ck_ml_pipeline_run_status", "ml_pipeline_run", type_="check")
    op.execute(
        "UPDATE ml_pipeline_run "
        "SET status = 'READY_FOR_RELEASE' "
        "WHERE status = 'DEPLOY_COMPLETED'"
    )
    op.create_check_constraint(
        "ck_ml_pipeline_run_status",
        "ml_pipeline_run",
        _check_values("status", OLD_ML_RUN_STATUSES),
    )
