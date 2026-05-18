from datetime import UTC, datetime

import pytest

from src.dataset.artifacts import DatasetArtifactWriter
from src.dataset.manifest import DatasetEligibilityPolicy
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


def _small_fixture_policy() -> DatasetEligibilityPolicy:
    return DatasetEligibilityPolicy(min_training_group_count=1, min_negative_count=1)


def test_materializer_emits_one_retrieval_group_for_like_used_and_topk_ids() -> None:
    dataset = DatasetMaterializer(eligibility_policy=_small_fixture_policy()).materialize(
        [
            _event(
                "event-1",
                created_at="2026-05-03T02:00:00Z",
                topk_ids=["chunk-pos", "chunk-neg-1", "chunk-neg-2"],
                used_ids=["chunk-pos"],
            ),
        ],
        chunk_text_by_id={
            "chunk-pos": "positive chunk text",
            "chunk-neg-1": "negative chunk text 1",
            "chunk-neg-2": "negative chunk text 2",
        },
        dataset_version="dataset-20260503T020000Z",
        created_at=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
        source_window_start=datetime(2026, 5, 3, 0, 0, tzinfo=UTC),
        source_window_end=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
    )

    assert dataset.manifest.training_group_count == 1
    assert dataset.manifest.positive_count == 1
    assert dataset.manifest.negative_count == 2
    assert dataset.manifest.negative_source_counts == {"exposed_unused": 2}
    assert dataset.manifest.missing_text_drop_count == 0
    assert dataset.eligible
    assert len(dataset.rows) == 1

    group = dataset.rows[0]
    assert group.query_text == "semantic search"
    assert group.source_event_ids == ("event-1",)
    assert group.project_id == "project-1"
    assert group.rating == "LIKE"
    assert group.feedback_created_at == datetime(2026, 5, 3, 2, 0, tzinfo=UTC)
    assert group.response_snapshot_ref == "snapshot:event-1"
    assert group.topk_ids == ("chunk-pos", "chunk-neg-1", "chunk-neg-2")
    assert group.used_ids == ("chunk-pos",)
    assert group.source_active_model_version == "embedding-v1"
    assert group.source_active_index_name == "index-v1"
    assert group.generation_rule_version == "retrieval-group-v1"
    assert group.positives[0].chunk_id == "chunk-pos"
    assert group.positives[0].text == "positive chunk text"
    assert group.positives[0].source == "liked_response_used_chunk"
    assert group.positives[0].confidence == pytest.approx(0.8)
    assert [negative.chunk_id for negative in group.negatives] == ["chunk-neg-1", "chunk-neg-2"]
    assert {negative.source for negative in group.negatives} == {"exposed_unused"}
    assert [negative.confidence for negative in group.negatives] == [pytest.approx(0.4), pytest.approx(0.4)]


def test_materializer_keeps_exposed_unused_in_single_group() -> None:
    dataset = DatasetMaterializer(eligibility_policy=_small_fixture_policy()).materialize(
        [
            _event(
                "event-1",
                created_at="2026-05-03T02:00:00Z",
                topk_ids=["chunk-pos-1", "chunk-pos-2", "chunk-neg-1", "chunk-neg-2"],
                used_ids=["chunk-pos-1", "chunk-pos-2"],
            ),
        ],
        chunk_text_by_id={
            "chunk-pos-1": "positive chunk text 1",
            "chunk-pos-2": "positive chunk text 2",
            "chunk-neg-1": "negative chunk text 1",
            "chunk-neg-2": "negative chunk text 2",
        },
        dataset_version="dataset-20260503T020000Z",
        created_at=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
        source_window_start=datetime(2026, 5, 3, 0, 0, tzinfo=UTC),
        source_window_end=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
    )

    assert len(dataset.rows) == 1
    assert [positive.chunk_id for positive in dataset.rows[0].positives] == ["chunk-pos-1", "chunk-pos-2"]
    assert [negative.chunk_id for negative in dataset.rows[0].negatives] == ["chunk-neg-1", "chunk-neg-2"]


def test_materializer_drops_missing_candidate_text_and_counts_drops() -> None:
    dataset = DatasetMaterializer(eligibility_policy=_small_fixture_policy()).materialize(
        [
            _event(
                "event-1",
                created_at="2026-05-03T02:00:00Z",
                topk_ids=["chunk-pos-1", "chunk-pos-missing", "chunk-neg-1", "chunk-neg-missing"],
                used_ids=["chunk-pos-1", "chunk-pos-missing"],
            ),
        ],
        chunk_text_by_id={
            "chunk-pos-1": "positive chunk text 1",
            "chunk-neg-1": "negative chunk text 1",
        },
        dataset_version="dataset-20260503T020000Z",
        created_at=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
        source_window_start=datetime(2026, 5, 3, 0, 0, tzinfo=UTC),
        source_window_end=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
    )

    assert dataset.manifest.training_group_count == 1
    assert dataset.manifest.positive_count == 1
    assert dataset.manifest.negative_count == 1
    assert dataset.manifest.missing_text_drop_count == 2
    assert [positive.chunk_id for positive in dataset.rows[0].positives] == ["chunk-pos-1"]
    assert [negative.chunk_id for negative in dataset.rows[0].negatives] == ["chunk-neg-1"]


def test_materializer_adds_same_project_random_negatives_with_ratio_and_bounds() -> None:
    dataset = DatasetMaterializer(eligibility_policy=_small_fixture_policy()).materialize(
        [
            _event(
                "event-1",
                created_at="2026-05-03T02:00:00Z",
                topk_ids=["chunk-pos", "chunk-neg-1", "chunk-neg-2"],
                used_ids=["chunk-pos"],
            ),
        ],
        chunk_text_by_id={
            "chunk-pos": "positive chunk text",
            "chunk-neg-1": "negative chunk text 1",
            "chunk-neg-2": "negative chunk text 2",
        },
        random_negative_pool_by_project_id={
            "project-1": {
                "chunk-pos": "positive chunk text",
                "chunk-neg-1": "negative chunk text 1",
                "chunk-neg-2": "negative chunk text 2",
                "chunk-random-1": "random chunk text 1",
                "chunk-random-2": "random chunk text 2",
            }
        },
        dataset_version="dataset-20260503T020000Z",
        created_at=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
        source_window_start=datetime(2026, 5, 3, 0, 0, tzinfo=UTC),
        source_window_end=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
    )

    group = dataset.rows[0]

    assert dataset.manifest.negative_count == 3
    assert dataset.manifest.negative_source_counts == {
        "exposed_unused": 2,
        "random_same_project": 1,
    }
    assert [negative.source for negative in group.negatives] == [
        "exposed_unused",
        "exposed_unused",
        "random_same_project",
    ]
    assert group.negatives[-1].chunk_id.startswith("chunk-random-")
    assert group.negatives[-1].confidence == pytest.approx(0.2)


def test_materializer_caps_same_project_random_negatives_at_three() -> None:
    dataset = DatasetMaterializer(eligibility_policy=_small_fixture_policy()).materialize(
        [
            _event(
                "event-1",
                created_at="2026-05-03T02:00:00Z",
                topk_ids=[
                    "chunk-pos",
                    "chunk-neg-1",
                    "chunk-neg-2",
                    "chunk-neg-3",
                    "chunk-neg-4",
                    "chunk-neg-5",
                    "chunk-neg-6",
                ],
                used_ids=["chunk-pos"],
            ),
        ],
        chunk_text_by_id={
            "chunk-pos": "positive chunk text",
            "chunk-neg-1": "negative chunk text 1",
            "chunk-neg-2": "negative chunk text 2",
            "chunk-neg-3": "negative chunk text 3",
            "chunk-neg-4": "negative chunk text 4",
            "chunk-neg-5": "negative chunk text 5",
            "chunk-neg-6": "negative chunk text 6",
        },
        random_negative_pool_by_project_id={
            "project-1": {
                "chunk-random-1": "random chunk text 1",
                "chunk-random-2": "random chunk text 2",
                "chunk-random-3": "random chunk text 3",
                "chunk-random-4": "random chunk text 4",
            }
        },
        dataset_version="dataset-20260503T020000Z",
        created_at=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
        source_window_start=datetime(2026, 5, 3, 0, 0, tzinfo=UTC),
        source_window_end=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
    )

    random_negatives = [
        negative
        for negative in dataset.rows[0].negatives
        if negative.source == "random_same_project"
    ]

    assert len(random_negatives) == 3


def test_materializer_dedupes_by_latest_event_and_writes_group_context() -> None:
    events = [
        _event("event-1", created_at="2026-05-03T01:00:00Z", query_text="old duplicate"),
        _event("event-1", created_at="2026-05-03T02:00:00Z", query_text="latest duplicate"),
        _event("event-2", created_at="2026-05-03T03:00:00Z", rating="DISLIKE"),
        _event("event-3", created_at="2026-05-03T04:00:00Z", topk_ids=["chunk-pos"], used_ids=["chunk-pos"]),
    ]

    dataset = DatasetMaterializer(eligibility_policy=_small_fixture_policy()).materialize(
        events,
        chunk_text_by_id={
            "chunk-pos": "positive chunk text",
            "chunk-neg": "negative chunk text",
        },
        dataset_version="dataset-20260503T020000Z",
        created_at=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
        source_window_start=datetime(2026, 5, 3, 0, 0, tzinfo=UTC),
        source_window_end=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
    )

    assert dataset.manifest.input_event_count == 4
    assert dataset.manifest.deduped_event_count == 3
    assert dataset.manifest.trainable_event_count == 1
    assert dataset.manifest.training_group_count == 1
    assert dataset.eligible
    assert [row.query_text for row in dataset.rows] == ["latest duplicate"]
    assert dataset.rows[0].positives[0].text == "positive chunk text"
    assert dataset.rows[0].negatives[0].text == "negative chunk text"
    assert dataset.rows[0].source_active_model_version == "embedding-v1"
    assert dataset.rows[0].source_active_index_name == "index-v1"
    assert dataset.rows[0].source_event_ids == ("event-1",)
    assert dataset.rows[0].req_id == "req-event-1"
    assert dataset.rows[0].user_id == "user-1"
    assert dataset.rows[0].rating == "LIKE"
    assert dataset.rows[0].feedback_created_at == datetime(2026, 5, 3, 2, 0, tzinfo=UTC)
    assert dataset.rows[0].response_snapshot_ref == "snapshot:event-1"
    assert dataset.rows[0].topk_ids == ("chunk-pos", "chunk-neg")
    assert dataset.rows[0].used_ids == ("chunk-pos",)


def test_materializer_outputs_do_not_expose_mutable_sequence_boundaries() -> None:
    dataset = DatasetMaterializer(eligibility_policy=_small_fixture_policy()).materialize(
        [
            _event(
                "event-1",
                created_at="2026-05-03T02:00:00Z",
                topk_ids=["chunk-pos", "chunk-neg"],
                used_ids=["chunk-pos"],
            ),
        ],
        chunk_text_by_id={
            "chunk-pos": "positive chunk text",
            "chunk-neg": "negative chunk text",
        },
        dataset_version="dataset-20260503T020000Z",
        created_at=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
        source_window_start=datetime(2026, 5, 3, 0, 0, tzinfo=UTC),
        source_window_end=datetime(2026, 5, 3, 5, 0, tzinfo=UTC),
    )

    group = dataset.rows[0]

    with pytest.raises(AttributeError):
        dataset.rows.append(group)
    with pytest.raises(AttributeError):
        group.positives.append(group.positives[0])
    with pytest.raises(AttributeError):
        group.negatives.append(group.negatives[0])
    with pytest.raises(AttributeError):
        group.source_event_ids.append("event-2")
    with pytest.raises(AttributeError):
        group.topk_ids.append("chunk-late")
    with pytest.raises(AttributeError):
        group.used_ids.append("chunk-late")

    serialized = group.to_dict()
    assert serialized["source_event_ids"] == ["event-1"]
    assert serialized["topk_ids"] == ["chunk-pos", "chunk-neg"]
    assert serialized["used_ids"] == ["chunk-pos"]


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
    dataset = DatasetMaterializer(eligibility_policy=_small_fixture_policy()).materialize(
        events,
        chunk_text_by_id={
            "chunk-pos": "positive chunk text",
            "chunk-neg": "negative chunk text",
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
    assert b'"positives":[{"chunk_id":"chunk-pos","confidence":0.8' in store.objects[refs.rows_storage_path]
    assert b'"negatives":[{"chunk_id":"chunk-neg","confidence":0.4' in store.objects[refs.rows_storage_path]
    assert b'"source_event_ids":["event-1"]' in store.objects[refs.rows_storage_path]
    assert b'"req_id":"req-1"' in store.objects[refs.rows_storage_path]
    assert b'"user_id":"user-1"' in store.objects[refs.rows_storage_path]
    assert b'"rating":"LIKE"' in store.objects[refs.rows_storage_path]
    assert b'"feedback_created_at":"2026-05-03T01:00:00+00:00"' in store.objects[refs.rows_storage_path]
    assert b'"response_snapshot_ref":"snapshot:event-1"' in store.objects[refs.rows_storage_path]
    assert b'"topk_ids":["chunk-pos","chunk-neg"]' in store.objects[refs.rows_storage_path]
    assert b'"used_ids":["chunk-pos"]' in store.objects[refs.rows_storage_path]
    assert b'"training_group_count":1' in store.objects[refs.manifest_storage_path]
    assert refs.manifest_uri == "gs://test-bucket/feedback/datasets/dataset-20260503T010000Z/manifest.json"
