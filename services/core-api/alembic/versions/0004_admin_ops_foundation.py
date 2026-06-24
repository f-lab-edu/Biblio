"""add admin ops foundation schema

Revision ID: 0004_admin_ops_foundation
Revises: 0003_pgvector_vector_index_entry
Create Date: 2026-04-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_admin_ops_foundation"
down_revision = "0003_pgvector_vector_index_entry"
branch_labels = None
depends_on = None


PROJECT_SERVING_STATES = ("SERVABLE", "ROLLBACK_EXCLUDED")
ML_RUN_STATUSES = ("PENDING", "RUNNING", "READY_FOR_RELEASE", "FAILED", "SUPERSEDED")
ML_FAILURE_TYPES = ("FAIL", "ERROR")
EVALUATION_STATUSES = ("RUNNING", "COMPLETED", "FAILED")
EVALUATION_DECISIONS = ("PASS", "FAIL")
RELEASE_STATUSES = ("STABLE", "CANDIDATE_REINDEXING", "ROLLBACK_PREPARING")


def _check_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted_values})"


def upgrade() -> None:
    op.create_table(
        "project",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("search_serving_state", sa.Text(), nullable=False, server_default=sa.text("'SERVABLE'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            _check_values("search_serving_state", PROJECT_SERVING_STATES),
            name="ck_project_search_serving_state",
        ),
    )
    op.execute("CREATE INDEX idx_project_user_created ON project(user_id, created_at DESC, id DESC)")
    op.create_index("idx_project_search_serving_state", "project", ["search_serving_state"])

    op.create_table(
        "search_response_snapshot",
        sa.Column("req_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("topk_chunk_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("used_chunk_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active_model_version", sa.String(length=128), nullable=False),
        sa.Column("active_index_name", sa.String(length=128), nullable=False),
        sa.Column("served_vector_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("project_serving_state", sa.Text(), nullable=False),
        sa.Column("scope_notice", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            _check_values("project_serving_state", PROJECT_SERVING_STATES),
            name="ck_search_response_snapshot_project_serving_state",
        ),
    )
    op.create_index("idx_search_response_snapshot_user_req", "search_response_snapshot", ["user_id", "req_id"])
    op.create_index("idx_search_response_snapshot_expires_at", "search_response_snapshot", ["expires_at"])

    op.create_table(
        "model_evaluation",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("candidate_model_version", sa.String(length=128), nullable=False),
        sa.Column("baseline_model_version", sa.String(length=128), nullable=False),
        sa.Column("evaluation_dataset_ref", sa.Text(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("quality_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pass_criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("overall_decision", sa.Text(), nullable=True),
        sa.Column("fail_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(_check_values("status", EVALUATION_STATUSES), name="ck_model_evaluation_status"),
        sa.CheckConstraint(
            "overall_decision IS NULL OR " + _check_values("overall_decision", EVALUATION_DECISIONS),
            name="ck_model_evaluation_overall_decision",
        ),
        sa.CheckConstraint("sample_count >= 0", name="ck_model_evaluation_sample_count"),
    )

    op.create_table(
        "ml_pipeline_run",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("failed_stage", sa.Text(), nullable=True),
        sa.Column("failure_type", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("candidate_model_version", sa.String(length=128), nullable=True),
        sa.Column("candidate_index_name", sa.String(length=128), nullable=True),
        sa.Column("baseline_model_version", sa.String(length=128), nullable=False),
        sa.Column("dataset_version", sa.String(length=128), nullable=False),
        sa.Column("evaluation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_evaluation.id"), nullable=True),
        sa.Column("cutover_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(_check_values("status", ML_RUN_STATUSES), name="ck_ml_pipeline_run_status"),
        sa.CheckConstraint(
            "failure_type IS NULL OR " + _check_values("failure_type", ML_FAILURE_TYPES),
            name="ck_ml_pipeline_run_failure_type",
        ),
    )
    op.create_foreign_key(
        "fk_ml_pipeline_run_superseded_by_run_id",
        "ml_pipeline_run",
        "ml_pipeline_run",
        ["superseded_by_run_id"],
        ["id"],
    )
    op.create_index("idx_ml_pipeline_run_status_created", "ml_pipeline_run", ["status", "created_at"])
    op.create_index("uq_ml_pipeline_run_running", "ml_pipeline_run", ["status"], unique=True, postgresql_where=sa.text("status = 'RUNNING'"))
    op.create_index("uq_ml_pipeline_run_pending", "ml_pipeline_run", ["status"], unique=True, postgresql_where=sa.text("status = 'PENDING'"))

    op.create_table(
        "model_release",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "singleton_key",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("release_status", sa.Text(), nullable=False, server_default=sa.text("'STABLE'")),
        sa.Column("active_model_version", sa.String(length=128), nullable=False),
        sa.Column("active_index_name", sa.String(length=128), nullable=False),
        sa.Column("previous_model_version", sa.String(length=128), nullable=True),
        sa.Column("previous_index_name", sa.String(length=128), nullable=True),
        sa.Column("candidate_model_version", sa.String(length=128), nullable=True),
        sa.Column("candidate_index_name", sa.String(length=128), nullable=True),
        sa.Column("rollback_snapshot_active_model_version", sa.String(length=128), nullable=True),
        sa.Column("rollback_snapshot_active_index_name", sa.String(length=128), nullable=True),
        sa.Column("rollback_snapshot_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candidate_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candidate_ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("switched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("singleton_key = 1", name="ck_model_release_singleton_key"),
        sa.CheckConstraint(_check_values("release_status", RELEASE_STATUSES), name="ck_model_release_status"),
        sa.UniqueConstraint("singleton_key", name="uq_model_release_singleton_key"),
    )
    op.create_index("idx_model_release_status", "model_release", ["release_status"])

    op.add_column(
        "video",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_video_project_id",
        "video",
        "project",
        ["project_id"],
        ["id"],
    )
    op.create_index("idx_video_project_id", "video", ["project_id"])
    op.add_column(
        "vector_index_entry",
        sa.Column(
            "index_name",
            sa.String(length=128),
            nullable=False,
            server_default=sa.text("'default-index'"),
        ),
    )
    op.add_column(
        "vector_index_entry",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE vector_index_entry AS vie
        SET project_id = v.project_id
        FROM video AS v
        WHERE vie.video_id = v.id
        """
    )
    op.create_foreign_key(
        "fk_vector_index_entry_project_id",
        "vector_index_entry",
        "project",
        ["project_id"],
        ["id"],
    )
    op.drop_constraint("vector_index_entry_pkey", "vector_index_entry", type_="primary")
    op.create_primary_key(
        "vector_index_entry_pkey",
        "vector_index_entry",
        ["index_name", "chunk_id"],
    )
    op.create_index(
        "idx_vector_index_entry_project_index",
        "vector_index_entry",
        ["project_id", "index_name"],
    )


def downgrade() -> None:
    op.drop_index("idx_vector_index_entry_project_index", table_name="vector_index_entry")
    op.drop_constraint("vector_index_entry_pkey", "vector_index_entry", type_="primary")
    op.execute("DELETE FROM vector_index_entry WHERE index_name != 'default-index'")
    op.create_primary_key("vector_index_entry_pkey", "vector_index_entry", ["chunk_id"])
    op.drop_constraint(
        "fk_vector_index_entry_project_id",
        "vector_index_entry",
        type_="foreignkey",
    )
    op.drop_column("vector_index_entry", "project_id")
    op.drop_column("vector_index_entry", "index_name")
    op.drop_index("idx_video_project_id", table_name="video")
    op.drop_constraint("fk_video_project_id", "video", type_="foreignkey")
    op.drop_column("video", "project_id")
    op.drop_index("idx_model_release_status", table_name="model_release")
    op.drop_table("model_release")
    op.drop_index("uq_ml_pipeline_run_pending", table_name="ml_pipeline_run")
    op.drop_index("uq_ml_pipeline_run_running", table_name="ml_pipeline_run")
    op.drop_index("idx_ml_pipeline_run_status_created", table_name="ml_pipeline_run")
    op.drop_table("ml_pipeline_run")
    op.drop_table("model_evaluation")
    op.drop_index("idx_search_response_snapshot_expires_at", table_name="search_response_snapshot")
    op.drop_index("idx_search_response_snapshot_user_req", table_name="search_response_snapshot")
    op.drop_table("search_response_snapshot")
    op.drop_index("idx_project_search_serving_state", table_name="project")
    op.execute("DROP INDEX IF EXISTS idx_project_user_created")
    op.drop_table("project")
