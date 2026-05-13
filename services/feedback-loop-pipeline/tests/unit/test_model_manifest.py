from datetime import UTC, datetime

import pytest

from src.infra.storage.inmemory import InMemoryArtifactStore
from src.training.manifest import ModelArtifactManifest
from src.training.runner import LocalTrainingRunner, TrainingInput


def _manifest() -> ModelArtifactManifest:
    return ModelArtifactManifest(
        candidate_model_version="candidate-v1",
        baseline_model_version="baseline-v1",
        dataset_version="dataset-v1",
        evaluation_dataset_ref="gs://bucket/eval/eval-v1.json",
        training_config_hash="sha256:abc123",
        base_model_name="BAAI/bge-small-en-v1.5",
        embedding_dimension=384,
        artifact_format="local-smoke",
        created_at=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
    )


def test_model_manifest_round_trips_required_fields() -> None:
    manifest = _manifest()

    restored = ModelArtifactManifest.from_json(manifest.to_json())

    assert restored == manifest


def test_model_manifest_rejects_missing_required_field() -> None:
    manifest = _manifest().to_dict()
    del manifest["evaluation_dataset_ref"]

    with pytest.raises(ValueError, match="evaluation_dataset_ref"):
        ModelArtifactManifest.from_dict(manifest)


async def test_local_training_runner_writes_candidate_manifest_and_metadata(tmp_path) -> None:
    store = InMemoryArtifactStore(
        {
            "datasets/dataset-v1/train.jsonl": (
                b'{"query_text":"alpha beta","positive_text":"beta answer",'
                b'"hard_negative_text":"alpha distractor"}\n'
            )
        }
    )
    runner = LocalTrainingRunner(
        artifact_store=store,
        model_artifact_prefix="model_artifacts/candidates",
        base_model_name="BAAI/bge-small-en-v1.5",
        embedding_dimension=384,
        artifact_format="local-smoke",
    )

    output = await runner.train(
        TrainingInput(
            ml_pipeline_run_id="run-1",
            trace_id="trace-1",
            dataset_version="dataset-v1",
            dataset_artifact_ref="gs://bucket/datasets/dataset-v1/train.jsonl",
            baseline_model_version="baseline-v1",
            candidate_model_version="candidate-v1",
            evaluation_dataset_ref="gs://bucket/eval/eval-v1.json",
            training_config_ref="configs/training/smoke.yaml",
            training_config_hash="sha256:abc123",
            started_at=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
        ),
        workspace_dir=tmp_path,
    )

    assert output.candidate_model_version == "candidate-v1"
    assert output.model_artifact_ref == (
        "gs://test-bucket/model_artifacts/candidates/candidate-v1/model_manifest.json"
    )
    assert output.training_metadata_ref == (
        "gs://test-bucket/model_artifacts/candidates/candidate-v1/training_metadata.json"
    )
    assert "model_artifacts/candidates/candidate-v1/model_manifest.json" in store.objects
    assert "model_artifacts/candidates/candidate-v1/training_metadata.json" in store.objects
    scoring_artifact = store.objects["model_artifacts/candidates/candidate-v1/scoring_artifact.json"]
    assert b'"model_version":"candidate-v1"' in scoring_artifact
    assert b'"beta"' in scoring_artifact
