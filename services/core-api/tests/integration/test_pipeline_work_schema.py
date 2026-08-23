import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.support import SessionFactory


@pytest.mark.asyncio
async def test_pipeline_work_tables_and_active_run_index_exist(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        table_rows = await session.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name LIKE 'pipeline_%'
                """
            )
        )
        index_rows = await session.execute(
            text(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'pipeline_run'
                """
            )
        )

    assert {row.table_name for row in table_rows} >= {
        "pipeline_run",
        "pipeline_audio_part",
        "pipeline_chunk_work",
        "pipeline_embedding_batch",
        "pipeline_stage_schedule",
    }
    indexes = {row.indexname: row.indexdef for row in index_rows}
    assert "uq_pipeline_run_active_video" in indexes
    assert "WHERE is_active" in indexes["uq_pipeline_run_active_video"]


@pytest.mark.asyncio
async def test_pipeline_run_accepts_cancelled_state(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        video_id = await session.scalar(
            text(
                """
                INSERT INTO video (user_id, title, category, input_type, status)
                VALUES (gen_random_uuid(), 'cancelled run test', 'GENERAL', 'LOCAL_FILE', 'DELETING')
                RETURNING id
                """
            )
        )
        run_id = await session.scalar(
            text(
                """
                INSERT INTO pipeline_run (video_id, pipeline_version)
                VALUES (:video_id, 'v1')
                RETURNING id
                """
            ),
            {"video_id": video_id},
        )
        await session.execute(
            text(
                """
                UPDATE pipeline_run
                SET status = 'CANCELLED',
                    is_active = false,
                    normalization_status = 'CANCELLED',
                    normalization_cancelled_at = NOW()
                WHERE id = :run_id
                """
            ),
            {"run_id": run_id},
        )
        await session.commit()


@pytest.mark.asyncio
async def test_video_accepts_work_unit_failed_stage(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO video (
                    user_id, title, category, input_type, status, failed_stage
                )
                VALUES (
                    gen_random_uuid(),
                    'transcription failure test',
                    'GENERAL',
                    'LOCAL_FILE',
                    'FAILED',
                    'TRANSCRIBE_PART'
                )
                """
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_pipeline_work_schema_rejects_duplicate_active_runs(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        video_id = await session.scalar(
            text(
                """
                INSERT INTO video (user_id, title, category, input_type, status)
                VALUES (gen_random_uuid(), 'pipeline schema test', 'GENERAL', 'LOCAL_FILE', 'PENDING')
                RETURNING id
                """
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO pipeline_run (video_id, pipeline_version)
                VALUES (:video_id, 'v1')
                """
            ),
            {"video_id": video_id},
        )
        await session.commit()

        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    """
                    INSERT INTO pipeline_run (video_id, pipeline_version)
                    VALUES (:video_id, 'v2')
                    """
                ),
                {"video_id": video_id},
            )
        await session.rollback()
