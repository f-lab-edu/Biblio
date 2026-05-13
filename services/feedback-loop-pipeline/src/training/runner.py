from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from src.infra.storage.client import ArtifactStore
from src.training.manifest import ModelArtifactManifest
from src.utils.text import tokens


@dataclass(frozen=True)
class TrainingInput:
    ml_pipeline_run_id: str
    trace_id: str
    dataset_version: str
    dataset_artifact_ref: str
    baseline_model_version: str
    candidate_model_version: str
    evaluation_dataset_ref: str
    training_config_ref: str
    training_config_hash: str
    started_at: datetime


@dataclass(frozen=True)
class TrainingOutput:
    candidate_model_version: str
    model_artifact_ref: str
    training_metadata_ref: str
    completed_at: datetime


class TrainingRunner(Protocol):
    async def train(self, training_input: TrainingInput, *, workspace_dir: Path) -> TrainingOutput: ...


class LocalTrainingRunner:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        model_artifact_prefix: str,
        base_model_name: str,
        embedding_dimension: int,
        artifact_format: str = "local-smoke",
    ) -> None:
        self._artifact_store = artifact_store
        self._model_artifact_prefix = model_artifact_prefix.rstrip("/")
        self._base_model_name = base_model_name
        self._embedding_dimension = embedding_dimension
        self._artifact_format = artifact_format

    async def train(self, training_input: TrainingInput, *, workspace_dir: Path) -> TrainingOutput:
        artifact_root = f"{self._model_artifact_prefix}/{training_input.candidate_model_version}"
        local_root = workspace_dir / "model_artifacts" / training_input.candidate_model_version
        local_root.mkdir(parents=True, exist_ok=True)
        manifest = ModelArtifactManifest(
            candidate_model_version=training_input.candidate_model_version,
            baseline_model_version=training_input.baseline_model_version,
            dataset_version=training_input.dataset_version,
            evaluation_dataset_ref=training_input.evaluation_dataset_ref,
            training_config_hash=training_input.training_config_hash,
            base_model_name=self._base_model_name,
            embedding_dimension=self._embedding_dimension,
            artifact_format=self._artifact_format,
            created_at=training_input.started_at,
        )
        manifest_path = local_root / "model_manifest.json"
        metadata_path = local_root / "training_metadata.json"
        scoring_artifact_path = local_root / "scoring_artifact.json"
        manifest_path.write_text(manifest.to_json(), encoding="utf-8")
        metadata_path.write_text(
            json.dumps(asdict(training_input), sort_keys=True, default=str, separators=(",", ":")),
            encoding="utf-8",
        )
        scoring_artifact_path.write_text(
            json.dumps(
                {
                    "artifact_format": "local-weighted-token-v1",
                    "model_version": training_input.candidate_model_version,
                    "term_weights": await self._learn_term_weights(training_input, workspace_dir=workspace_dir),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

        manifest_storage_path = f"{artifact_root}/model_manifest.json"
        metadata_storage_path = f"{artifact_root}/training_metadata.json"
        scoring_artifact_storage_path = f"{artifact_root}/scoring_artifact.json"
        await self._artifact_store.upload_object(manifest_path, manifest_storage_path)
        await self._artifact_store.upload_object(metadata_path, metadata_storage_path)
        await self._artifact_store.upload_object(scoring_artifact_path, scoring_artifact_storage_path)
        return TrainingOutput(
            candidate_model_version=training_input.candidate_model_version,
            model_artifact_ref=self._artifact_store.object_uri(manifest_storage_path),
            training_metadata_ref=self._artifact_store.object_uri(metadata_storage_path),
            completed_at=training_input.started_at,
        )

    async def _learn_term_weights(self, training_input: TrainingInput, *, workspace_dir: Path) -> dict[str, float]:
        storage_path = _storage_path_from_ref(training_input.dataset_artifact_ref)
        local_path = workspace_dir / "training_dataset" / Path(storage_path).name
        try:
            await self._artifact_store.download_object(storage_path, local_path)
        except FileNotFoundError:
            return {}

        weights: dict[str, float] = {}
        for line in local_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            query_tokens = set(tokens(str(row.get("query_text", ""))))
            positive_tokens = set(tokens(str(row.get("positive_text", ""))))
            negative_tokens = set(tokens(str(row.get("hard_negative_text", ""))))
            for token in query_tokens & positive_tokens:
                weights[token] = weights.get(token, 1.0) + 1.0
            for token in query_tokens & negative_tokens:
                weights[token] = max(0.1, weights.get(token, 1.0) - 0.25)
        return {token: weights[token] for token in sorted(weights)}


def _storage_path_from_ref(artifact_ref: str) -> str:
    if not artifact_ref.startswith("gs://"):
        return artifact_ref
    bucket_and_path = artifact_ref.removeprefix("gs://")
    _, separator, storage_path = bucket_and_path.partition("/")
    if not separator or not storage_path:
        raise ValueError(f"Invalid artifact ref: {artifact_ref}")
    return storage_path
