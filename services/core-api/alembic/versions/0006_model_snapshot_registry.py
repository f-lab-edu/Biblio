"""add model_snapshot registry and drop single-generation rollback snapshot columns

Revision ID: 0006_model_snapshot_registry
Revises: 0005_legacy_reindex_metadata
Create Date: 2026-06-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_model_snapshot_registry"
down_revision = "0005_legacy_reindex_metadata"
branch_labels = None
depends_on = None

SNAPSHOT_STATUSES = ("ACTIVE", "PREVIOUS_STABLE", "ROLLED_BACK", "SUPERSEDED")


def _check_values(column_name: str, values: tuple[str, ...]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted_values})"


def upgrade() -> None:
    op.create_table(
        "model_snapshot",
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("index_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "previous_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_snapshot.snapshot_id"),
            nullable=True,
        ),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            _check_values("status", SNAPSHOT_STATUSES),
            name="ck_model_snapshot_status",
        ),
    )
    op.create_index(
        "uq_model_snapshot_active",
        "model_snapshot",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "uq_model_snapshot_previous_stable",
        "model_snapshot",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'PREVIOUS_STABLE'"),
    )

    op.execute(
        """
        INSERT INTO model_snapshot (snapshot_id, model_version, index_name, status, captured_at, created_at)
        SELECT gen_random_uuid(), active_model_version, active_index_name, 'ACTIVE', NOW(), NOW()
        FROM model_release
        WHERE singleton_key = 1
        """
    )

    op.drop_column("model_release", "rollback_snapshot_active_model_version")
    op.drop_column("model_release", "rollback_snapshot_active_index_name")
    op.drop_column("model_release", "rollback_snapshot_captured_at")


def downgrade() -> None:
    op.add_column(
        "model_release",
        sa.Column("rollback_snapshot_captured_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "model_release",
        sa.Column("rollback_snapshot_active_index_name", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "model_release",
        sa.Column("rollback_snapshot_active_model_version", sa.String(length=128), nullable=True),
    )
    op.drop_index("uq_model_snapshot_previous_stable", table_name="model_snapshot")
    op.drop_index("uq_model_snapshot_active", table_name="model_snapshot")
    op.drop_table("model_snapshot")
