import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.support import SessionFactory


@pytest.mark.asyncio
async def test_admin_ops_foundation_schema_contract(session_factory: SessionFactory) -> None:
    async with session_factory() as session:
        project_columns = await _columns_for(session, "project")
        video_columns = await _columns_for(session, "video")
        snapshot_columns = await _columns_for(session, "search_response_snapshot")
        run_columns = await _columns_for(session, "ml_pipeline_run")
        evaluation_columns = await _columns_for(session, "model_evaluation")
        release_columns = await _columns_for(session, "model_release")
        catalog_columns = await _columns_for(session, "vector_index_catalog")
        reindex_columns = await _columns_for(session, "legacy_reindex_item")
        project_state_default = await session.scalar(
            text(
                """
                SELECT column_default
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'project'
                  AND column_name = 'search_serving_state'
                """
            )
        )
        project_state_check = await _constraint_definition(session, "ck_project_search_serving_state")
        ml_run_status_check = await _constraint_definition(session, "ck_ml_pipeline_run_status")
        release_status_check = await _constraint_definition(session, "ck_model_release_status")
        release_singleton_check = await _constraint_definition(
            session, "ck_model_release_singleton_key"
        )
        reindex_status_check = await _constraint_definition(session, "ck_legacy_reindex_item_status")

    assert project_columns["search_serving_state"][1] == "text"
    assert video_columns["project_id"][1] == "uuid"
    assert project_state_default == "'SERVABLE'::text"
    assert "SERVABLE" in project_state_check
    assert "ROLLBACK_EXCLUDED" in project_state_check
    assert snapshot_columns["req_id"][1] == "uuid"
    assert snapshot_columns["served_vector_paths"][0] == "jsonb"
    assert evaluation_columns["quality_metrics"][0] == "jsonb"
    assert run_columns["status"][1] == "text"
    assert run_columns["baseline_model_version"][1] == "varchar"
    assert "READY_FOR_RELEASE" in ml_run_status_check
    assert release_columns["singleton_key"][1] == "int2"
    assert release_columns["release_status"][1] == "text"
    assert "ROLLBACK_PREPARING" in release_status_check
    assert "singleton_key = 1" in release_singleton_check
    assert catalog_columns["index_name"][1] == "varchar"
    assert catalog_columns["embedding_dimension"][1] == "int4"
    assert reindex_columns["source_index_name"][1] == "varchar"
    assert reindex_columns["target_index_name"][1] == "varchar"
    assert "SKIPPED" in reindex_status_check


@pytest.mark.asyncio
async def test_sqlalchemy_models_include_admin_ops_foundation_tables() -> None:
    from src.models import admin_ops  # noqa: F401
    from src.models.video import Base

    table_names = set(Base.metadata.tables)

    assert {
        "project",
        "search_response_snapshot",
        "ml_pipeline_run",
        "model_evaluation",
        "model_release",
        "vector_index_catalog",
        "legacy_reindex_item",
    }.issubset(table_names)
    assert "project_id" in Base.metadata.tables["video"].columns
    model_release_table = Base.metadata.tables["model_release"]
    model_release_constraints = {
        constraint.name for constraint in model_release_table.constraints
    }
    assert "singleton_key" in model_release_table.columns
    assert "ck_model_release_singleton_key" in model_release_constraints
    assert "uq_model_release_singleton_key" in model_release_constraints
    legacy_reindex_table = Base.metadata.tables["legacy_reindex_item"]
    legacy_reindex_constraints = {
        constraint.name for constraint in legacy_reindex_table.constraints
    }
    assert "ck_legacy_reindex_item_status" in legacy_reindex_constraints
    assert "uq_legacy_reindex_item_video_source_target" in legacy_reindex_constraints


@pytest.mark.asyncio
async def test_model_release_schema_rejects_duplicate_singleton_rows(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO model_release (
                    active_model_version,
                    active_index_name
                )
                VALUES ('model-v1', 'index-v1')
                """
            )
        )
        await session.commit()

        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    """
                    INSERT INTO model_release (
                        active_model_version,
                        active_index_name
                    )
                    VALUES ('model-v2', 'index-v2')
                    """
                )
            )
            await session.commit()
        await session.rollback()


async def _columns_for(session, table_name: str) -> dict[str, tuple[str, str]]:
    result = await session.execute(
        text(
            """
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :table_name
            ORDER BY ordinal_position
            """
        ),
        {"table_name": table_name},
    )
    return {row.column_name: (row.data_type, row.udt_name) for row in result}


async def _constraint_definition(session, constraint_name: str) -> str:
    definition = await session.scalar(
        text(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = :constraint_name
            """
        ),
        {"constraint_name": constraint_name},
    )
    assert definition is not None
    return definition
