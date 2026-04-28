from src.infra.db.models import Base


def test_vector_index_entry_model_matches_release_serving_contract() -> None:
    table = Base.metadata.tables["vector_index_entry"]

    assert "index_name" in table.columns
    assert "project_id" in table.columns
    assert [column.name for column in table.primary_key.columns] == [
        "index_name",
        "chunk_id",
    ]
