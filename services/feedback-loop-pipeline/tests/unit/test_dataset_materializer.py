from datetime import UTC, datetime

from src.dataset.artifacts import DatasetArtifactWriter
from src.dataset.materializer import DatasetMaterializer
from src.dataset.raw_log import RawFeedbackLogReader
from src.infra.storage.inmemory import InMemoryArtifactStore


def _event(
    event_id: str,
    *,
    created_at: str,
    rating: str = "LIKE",
    query_text: str = "semantic search",
    topk_ids: list[str] | None = None,
    used_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "req_id": f"req-{event_id}",
        "user_id": "user-1",
        "project_id": "project-1",
        "created_at": created_at,
        "trace_id": f"trace-{event_id}",
        "rating": rating,
        "query_text": query_text,
        "topk_ids": topk_ids or ["chunk-pos", "chunk-neg"],
        "used_ids": used_ids or ["chunk-pos"],
        "active_model_version": "embedding-v1",
        "active_index_name": "index-v1",
        "response_snapshot_ref": f"snapshot:{event_id}",
    }


def test_materializer_dedupes_by_latest_event_and_writes_text_snapshots() -> None:
    events = [
        _event("event-1", created_at="2026-05-03T01:00:00Z", query_text="old duplicate"),
        _event("event-1", created_at="2026-05-03T02:00:00Z", query_text="latest duplicate"),
        _event("event-2", created_at="2026-05-03T03:00:00Z", rating="DISLIKE"),
        _event("event-3", created_at="2026-05-03T04:00:00Z", topk_ids=["chunk-pos"], used_ids=["chunk-pos"]),
    ]

    dataset = DatasetMaterializer().materialize(
        events,
        chunk_text_by_id={
            "chunk-pos": "positive chunk text",
            "chunk-neg": "hard negative chunk text",
        },
        dataset_version="dataset-20260503T020000Z",
        created_at=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
        source_window_start=datetime(2026, 5, 3, 0, 0, tzinfo=UTC),
        source_window_end=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
    )

    assert dataset.manifest.input_event_count == 4
    assert dataset.manifest.deduped_event_count == 3
    assert dataset.manifest.trainable_event_count == 1
    assert dataset.manifest.triplet_row_count == 1
    assert dataset.eligible
    assert [row.query_text for row in dataset.rows] == ["latest duplicate"]
    assert dataset.rows[0].positive_text == "positive chunk text"
    assert dataset.rows[0].hard_negative_text == "hard negative chunk text"
    assert dataset.rows[0].source_active_model_version == "embedding-v1"
    assert dataset.rows[0].source_active_index_name == "index-v1"
    assert dataset.rows[0].trace_id == "trace-event-1"
    assert dataset.rows[0].req_id == "req-event-1"
    assert dataset.rows[0].user_id == "user-1"
    assert dataset.rows[0].rating == "LIKE"
    assert dataset.rows[0].feedback_created_at == datetime(2026, 5, 3, 2, 0, tzinfo=UTC)
    assert dataset.rows[0].response_snapshot_ref == "snapshot:event-1"
    assert dataset.rows[0].topk_ids == ["chunk-pos", "chunk-neg"]
    assert dataset.rows[0].used_ids == ["chunk-pos"]


async def test_raw_log_reader_and_artifact_writer_round_trip_dataset_jsonl(tmp_path) -> None:
    raw_line = (
        '{"schema_version":1,"event_id":"event-1","req_id":"req-1","user_id":"user-1",'
        '"project_id":"project-1","created_at":"2026-05-03T01:00:00Z","trace_id":"trace-1","rating":"LIKE",'
        '"query_text":"semantic search","topk_ids":["chunk-pos","chunk-neg"],"used_ids":["chunk-pos"],'
        '"active_model_version":"embedding-v1","active_index_name":"index-v1",'
        '"response_snapshot_ref":"snapshot:event-1"}\n'
    )
    store = InMemoryArtifactStore(
        {
            "feedback/raw_logs/schema_version=1/ingest_date=2026-05-03/hour=01/events.jsonl": raw_line.encode(),
        }
    )

    events = await RawFeedbackLogReader(store).read_events(
        "feedback/raw_logs/schema_version=1/",
        workspace_dir=tmp_path,
    )
    dataset = DatasetMaterializer().materialize(
        events,
        chunk_text_by_id={
            "chunk-pos": "positive chunk text",
            "chunk-neg": "hard negative chunk text",
        },
        dataset_version="dataset-20260503T010000Z",
        created_at=datetime(2026, 5, 3, 1, 5, tzinfo=UTC),
        source_window_start=datetime(2026, 5, 3, 1, 0, tzinfo=UTC),
        source_window_end=datetime(2026, 5, 3, 2, 0, tzinfo=UTC),
    )

    refs = await DatasetArtifactWriter(store).write_dataset(
        dataset,
        artifact_prefix="feedback/datasets",
        workspace_dir=tmp_path,
    )

    assert refs.rows_storage_path == "feedback/datasets/dataset-20260503T010000Z/train.jsonl"
    assert refs.manifest_storage_path == "feedback/datasets/dataset-20260503T010000Z/manifest.json"
    assert b'"positive_text":"positive chunk text"' in store.objects[refs.rows_storage_path]
    assert b'"trace_id":"trace-1"' in store.objects[refs.rows_storage_path]
    assert b'"req_id":"req-1"' in store.objects[refs.rows_storage_path]
    assert b'"user_id":"user-1"' in store.objects[refs.rows_storage_path]
    assert b'"rating":"LIKE"' in store.objects[refs.rows_storage_path]
    assert b'"feedback_created_at":"2026-05-03T01:00:00+00:00"' in store.objects[refs.rows_storage_path]
    assert b'"response_snapshot_ref":"snapshot:event-1"' in store.objects[refs.rows_storage_path]
    assert b'"topk_ids":["chunk-pos","chunk-neg"]' in store.objects[refs.rows_storage_path]
    assert b'"used_ids":["chunk-pos"]' in store.objects[refs.rows_storage_path]
    assert b'"triplet_row_count":1' in store.objects[refs.manifest_storage_path]
    assert refs.manifest_uri == "gs://test-bucket/feedback/datasets/dataset-20260503T010000Z/manifest.json"
