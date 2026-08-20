from sqlalchemy import CheckConstraint

from src.infra.db.models import Base


def test_vector_index_entry_model_matches_release_serving_contract() -> None:
    table = Base.metadata.tables["vector_index_entry"]

    assert "index_name" in table.columns
    assert "project_id" in table.columns
    assert [column.name for column in table.primary_key.columns] == [
        "index_name",
        "chunk_id",
    ]


def test_pipeline_work_models_include_required_tables_and_constraints() -> None:
    assert {
        "pipeline_run",
        "pipeline_audio_part",
        "pipeline_chunk_work",
        "pipeline_embedding_batch",
        "pipeline_stage_schedule",
    }.issubset(Base.metadata.tables)
    run_index_names = {
        index.name for index in Base.metadata.tables["pipeline_run"].indexes
    }
    audio_constraint_names = {
        constraint.name
        for constraint in Base.metadata.tables["pipeline_audio_part"].constraints
    }
    chunk_constraint_names = {
        constraint.name
        for constraint in Base.metadata.tables["pipeline_chunk_work"].constraints
    }

    assert "uq_pipeline_run_active_video" in run_index_names
    assert "uq_pipeline_audio_part_run_index" in audio_constraint_names
    assert "uq_pipeline_chunk_work_run_index" in chunk_constraint_names


def test_pipeline_work_status_constraints_include_cancelled() -> None:
    status_constraint_names = {
        "pipeline_run": "ck_pipeline_run_status",
        "pipeline_audio_part": "ck_pipeline_audio_part_status",
        "pipeline_embedding_batch": "ck_pipeline_embedding_batch_status",
    }

    for table_name, constraint_name in status_constraint_names.items():
        constraint = next(
            constraint
            for constraint in Base.metadata.tables[table_name].constraints
            if constraint.name == constraint_name
        )
        assert "CANCELLED" in str(constraint.sqltext)

    chunk_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in Base.metadata.tables["pipeline_chunk_work"].constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }
    assert "CANCELLED" in chunk_constraints["ck_pipeline_chunk_work_enrichment_status"]
    assert "CANCELLED" in chunk_constraints["ck_pipeline_chunk_work_embedding_status"]
