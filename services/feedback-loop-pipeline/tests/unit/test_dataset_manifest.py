from datetime import UTC, datetime

import pytest

from src.dataset.manifest import (
    DatasetEligibilityPolicy,
    DatasetManifest,
    DatasetManifestSelector,
    is_manifest_eligible,
)
from src.infra.storage.inmemory import InMemoryArtifactStore


def _manifest(
    dataset_version: str,
    *,
    created_at: datetime,
    generation_rule_version: str = "retrieval-group-v1",
    trainable_event_count: int = 1,
    training_group_count: int = 10,
    positive_count: int = 10,
    negative_count: int = 20,
    negative_source_counts: dict[str, int] | None = None,
    missing_text_drop_count: int = 0,
) -> DatasetManifest:
    return DatasetManifest(
        dataset_version=dataset_version,
        created_at=created_at,
        generation_rule_version=generation_rule_version,
        source_window_start=datetime(2026, 5, 3, 0, 0, tzinfo=UTC),
        source_window_end=datetime(2026, 5, 3, 1, 0, tzinfo=UTC),
        input_event_count=2,
        deduped_event_count=2,
        trainable_event_count=trainable_event_count,
        training_group_count=training_group_count,
        positive_count=positive_count,
        negative_count=negative_count,
        negative_source_counts=negative_source_counts or {"exposed_unused": negative_count},
        missing_text_drop_count=missing_text_drop_count,
    )


def test_manifest_round_trips_group_counts_drop_counts_eligibility_and_reasons() -> None:
    manifest = _manifest(
        "dataset-20260503T010000Z",
        created_at=datetime(2026, 5, 3, 1, 0, tzinfo=UTC),
        training_group_count=12,
        positive_count=12,
        negative_count=24,
        negative_source_counts={"exposed_unused": 22, "random_same_project": 2},
        missing_text_drop_count=3,
    )

    restored = DatasetManifest.from_json(manifest.to_json())

    assert restored == manifest
    assert restored.eligible
    assert restored.ineligible_reasons == ()
    assert is_manifest_eligible(restored, DatasetEligibilityPolicy())
    with pytest.raises(TypeError):
        restored.negative_source_counts["late_source"] = 1


def test_default_policy_rejects_too_small_dataset_and_custom_policy_accepts_fixture() -> None:
    manifest = _manifest(
        "dataset-small",
        created_at=datetime(2026, 5, 3, 1, 0, tzinfo=UTC),
        training_group_count=1,
        positive_count=1,
        negative_count=1,
    )

    assert not is_manifest_eligible(manifest, DatasetEligibilityPolicy())
    assert is_manifest_eligible(
        manifest,
        DatasetEligibilityPolicy(min_training_group_count=1, min_negative_count=1),
    )


def test_manifest_records_ineligible_reasons_for_unsupported_rule_or_insufficient_counts() -> None:
    policy = DatasetEligibilityPolicy(min_training_group_count=2, min_negative_count=2)

    unsupported_rule = _manifest(
        "dataset-unsupported",
        created_at=datetime(2026, 5, 3, 1, 0, tzinfo=UTC),
        generation_rule_version="unsupported-v1",
        training_group_count=2,
        negative_count=2,
    )
    too_few_groups = _manifest(
        "dataset-too-small",
        created_at=datetime(2026, 5, 3, 2, 0, tzinfo=UTC),
        training_group_count=1,
        negative_count=2,
    )

    assert not is_manifest_eligible(unsupported_rule, policy)
    assert not is_manifest_eligible(too_few_groups, policy)
    assert unsupported_rule.with_eligibility(policy).ineligible_reasons == ("unsupported_generation_rule_version",)
    assert too_few_groups.with_eligibility(policy).ineligible_reasons == ("training_group_count_below_minimum",)


def test_manifest_rejects_payload_missing_group_counts() -> None:
    payload = (
        '{"dataset_version":"dataset-incomplete","created_at":"2026-05-03T01:00:00Z",'
        '"generation_rule_version":"retrieval-group-v1","source_window_start":"2026-05-03T00:00:00Z",'
        '"source_window_end":"2026-05-03T01:00:00Z","input_event_count":2,'
        '"deduped_event_count":2,"trainable_event_count":1,'
        '"negative_source_counts":{"exposed_unused":1}}'
    )

    with pytest.raises(KeyError, match="training_group_count"):
        DatasetManifest.from_json(payload)


async def test_manifest_selector_returns_latest_successful_eligible_dataset(tmp_path) -> None:
    older = _manifest(
        "dataset-older",
        created_at=datetime(2026, 5, 3, 1, 0, tzinfo=UTC),
    )
    latest = _manifest(
        "dataset-latest",
        created_at=datetime(2026, 5, 3, 3, 0, tzinfo=UTC),
    )
    ineligible = _manifest(
        "dataset-ineligible",
        created_at=datetime(2026, 5, 3, 4, 0, tzinfo=UTC),
        training_group_count=0,
    )
    store = InMemoryArtifactStore(
        {
            "feedback/datasets/dataset-older/manifest.json": older.to_json().encode(),
            "feedback/datasets/dataset-latest/manifest.json": latest.to_json().encode(),
            "feedback/datasets/dataset-ineligible/manifest.json": ineligible.to_json().encode(),
        }
    )

    selected = await DatasetManifestSelector(store).select_latest_eligible(
        "feedback/datasets",
        workspace_dir=tmp_path,
    )

    assert selected == latest


async def test_manifest_selector_skips_manifest_with_non_mapping_negative_source_counts(tmp_path) -> None:
    eligible = _manifest(
        "dataset-eligible",
        created_at=datetime(2026, 5, 3, 1, 0, tzinfo=UTC),
    )
    malformed = (
        '{"dataset_version":"dataset-malformed","created_at":"2026-05-03T03:00:00Z",'
        '"generation_rule_version":"retrieval-group-v1","source_window_start":"2026-05-03T00:00:00Z",'
        '"source_window_end":"2026-05-03T01:00:00Z","input_event_count":2,'
        '"deduped_event_count":2,"trainable_event_count":1,"training_group_count":10,'
        '"positive_count":10,"negative_count":20,"negative_source_counts":null,'
        '"missing_text_drop_count":0}'
    )
    store = InMemoryArtifactStore(
        {
            "feedback/datasets/dataset-eligible/manifest.json": eligible.to_json().encode(),
            "feedback/datasets/dataset-malformed/manifest.json": malformed.encode(),
        }
    )

    selected = await DatasetManifestSelector(store).select_latest_eligible(
        "feedback/datasets",
        workspace_dir=tmp_path,
    )

    assert selected == eligible
