"""migrate vector_index_entry to pgvector contract

Revision ID: 0003_pgvector_vector_index_entry
Revises: 0002_pipeline_worker_artifacts
Create Date: 2026-03-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_pgvector_vector_index_entry"
down_revision = "0002_pipeline_worker_artifacts"
branch_labels = None
depends_on = None


class PGVector(sa.types.UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        del kw
        return "vector"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "vector_index_entry",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "vector_index_entry",
        sa.Column("embedding_vector", PGVector(), nullable=True),
    )
    op.add_column(
        "vector_index_entry",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.execute(
        """
        UPDATE vector_index_entry AS vie
        SET user_id = v.user_id,
            embedding_vector = vie.embedding::text::vector
        FROM video AS v
        WHERE vie.video_id = v.id
        """
    )
    op.alter_column("vector_index_entry", "user_id", nullable=False)
    op.alter_column("vector_index_entry", "embedding_vector", nullable=False)
    op.drop_constraint("vector_index_entry_pkey", "vector_index_entry", type_="primary")
    op.create_primary_key("vector_index_entry_pkey", "vector_index_entry", ["chunk_id"])
    op.drop_column("vector_index_entry", "id")
    op.drop_column("vector_index_entry", "embedding")


def downgrade() -> None:
    op.add_column(
        "vector_index_entry",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.add_column(
        "vector_index_entry",
        sa.Column("embedding", sa.JSON(), nullable=True),
    )
    op.execute(
        """
        UPDATE vector_index_entry
        SET id = gen_random_uuid(),
            embedding = embedding_vector::text::json
        """
    )
    op.alter_column("vector_index_entry", "id", nullable=False)
    op.alter_column("vector_index_entry", "id", server_default=None)
    op.alter_column("vector_index_entry", "embedding", nullable=False)
    op.drop_constraint("vector_index_entry_pkey", "vector_index_entry", type_="primary")
    op.create_primary_key("vector_index_entry_pkey", "vector_index_entry", ["id"])
    op.drop_column("vector_index_entry", "created_at")
    op.drop_column("vector_index_entry", "embedding_vector")
    op.drop_column("vector_index_entry", "user_id")
