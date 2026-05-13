from datetime import UTC, datetime

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
    generation_rule_version: str = "triplet-v1",
    deduped_event_count: int = 2,
    trainable_event_count: int = 1,
    triplet_row_count: int = 1,
) -> DatasetManifest:
    return DatasetManifest(
        dataset_version=dataset_version,
        created_at=created_at,
        generation_rule_version=generation_rule_version,
        source_window_start=datetime(2026, 5, 3, 0, 0, tzinfo=UTC),
        source_window_end=datetime(2026, 5, 3, 1, 0, tzinfo=UTC),
        input_event_count=2,
        deduped_event_count=deduped_event_count,
        trainable_event_count=trainable_event_count,
        triplet_row_count=triplet_row_count,
    )


def test_manifest_round_trips_required_fields_and_eligibility() -> None:
    manifest = _manifest(
        "dataset-20260503T010000Z",
        created_at=datetime(2026, 5, 3, 1, 0, tzinfo=UTC),
    )

    restored = DatasetManifest.from_json(manifest.to_json())

    assert restored == manifest
    assert is_manifest_eligible(restored, DatasetEligibilityPolicy())


def test_manifest_rejects_unsupported_rule_or_insufficient_counts() -> None:
    policy = DatasetEligibilityPolicy(min_deduped_event_count=2, min_triplet_row_count=2)

    unsupported_rule = _manifest(
        "dataset-unsupported",
        created_at=datetime(2026, 5, 3, 1, 0, tzinfo=UTC),
        generation_rule_version="unknown-rule",
        triplet_row_count=2,
    )
    too_few_rows = _manifest(
        "dataset-too-small",
        created_at=datetime(2026, 5, 3, 2, 0, tzinfo=UTC),
        triplet_row_count=1,
    )

    assert not is_manifest_eligible(unsupported_rule, policy)
    assert not is_manifest_eligible(too_few_rows, policy)


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
        triplet_row_count=0,
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
