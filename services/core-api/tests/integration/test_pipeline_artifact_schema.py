import pytest
from sqlalchemy import text

from tests.support import SessionFactory


@pytest.mark.asyncio
async def test_alembic_creates_pgvector_vector_index_entry_contract(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        vector_extension = await session.scalar(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        )
        result = await session.execute(
            text(
                """
                SELECT column_name, data_type, udt_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'vector_index_entry'
                ORDER BY ordinal_position
                """
            )
        )
        columns = {
            row.column_name: (row.data_type, row.udt_name)
            for row in result
        }
        primary_key_columns = (
            await session.execute(
                text(
                    """
                    SELECT a.attname
                    FROM pg_index i
                    JOIN pg_class t ON t.oid = i.indrelid
                    JOIN pg_attribute a
                      ON a.attrelid = i.indrelid
                     AND a.attnum = ANY(i.indkey)
                    WHERE t.relname = 'vector_index_entry'
                      AND i.indisprimary
                    ORDER BY a.attnum
                    """
                )
            )
        ).scalars().all()

    assert vector_extension == "vector"
    assert "id" not in columns
    assert "embedding" not in columns
    assert columns["chunk_id"][1] == "uuid"
    assert columns["user_id"][1] == "uuid"
    assert columns["video_id"][1] == "uuid"
    assert columns["embedding_vector"][1] == "vector"
    assert columns["embedding_model_version"][1] == "varchar"
    assert columns["created_at"][1] == "timestamptz"
    assert primary_key_columns == ["chunk_id"]
