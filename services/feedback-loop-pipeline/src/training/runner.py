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


@dataclass(frozen=True)
class ClonedModelArtifactRefs:
    source_model_version: str
    source_artifact_ref: str
    candidate_artifact_ref: str
    serving_artifact_ref: str


class CloneActiveModel:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        serving_model_artifact_prefix: str,
    ) -> None:
        self._artifact_store = artifact_store
        self._serving_model_artifact_prefix = serving_model_artifact_prefix.rstrip("/")

    async def clone(self, *, active_model_version: str, candidate_model_version: str) -> ClonedModelArtifactRefs:
        source_prefix = self._serving_prefix(active_model_version)
        target_prefix = self._serving_prefix(candidate_model_version)
        await self._copy_serving_artifact_if_absent(source_prefix, target_prefix)
        # source_artifact_ref: 복제 대상 폴더 주소, candidate_artifact_ref: 복제하여 생성할 폴더 주소(ex:  gs://bucket/models/candidate/)
        source_artifact_ref = self._artifact_store.object_uri(source_prefix)
        candidate_artifact_ref = self._artifact_store.object_uri(target_prefix)
        return ClonedModelArtifactRefs(
            source_model_version=active_model_version,
            source_artifact_ref=source_artifact_ref,
            candidate_artifact_ref=candidate_artifact_ref,
            serving_artifact_ref=candidate_artifact_ref,
        )

    async def _copy_serving_artifact_if_absent(self, source_prefix: str, target_prefix: str) -> None:
        try:
            await self._artifact_store.copy_prefix(source_prefix, target_prefix)
        except FileExistsError:
            # 재시도로 같은 candidate 경로가 이미 복제돼 있으면 복제만 건너뛴다.
            # 복제 결과는 동일하므로 이후 학습 단계를 정상 진행한다.
            return

    def _serving_prefix(self, model_version: str) -> str:
        return f"{self._serving_model_artifact_prefix}/{model_version}/"


class LocalTrainingRunner:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        model_artifact_prefix: str, # 학습 산출물(meta data) 저장 폴더 경로(feedback/model-artifacts)
        base_model_name: str, #  base_model_name: 출처 모델 이름(BAAI/bge-m3)
        embedding_dimension: int,
        artifact_format: str = "local-smoke",
        serving_model_artifact_prefix: str | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._model_artifact_prefix = model_artifact_prefix.rstrip("/")
        # serving prefix가 주어지면 복제를 켜고, 없으면 끈다(복제 없이 흐름만 보는 테스트용).
        self._clone_active_model = (
            CloneActiveModel(
                artifact_store=artifact_store,
                serving_model_artifact_prefix=serving_model_artifact_prefix,
            )
            if serving_model_artifact_prefix is not None
            else None
        )
        self._base_model_name = base_model_name
        self._embedding_dimension = embedding_dimension
        self._artifact_format = artifact_format

    async def train(self, training_input: TrainingInput, *, workspace_dir: Path) -> TrainingOutput:
        artifact_root = f"{self._model_artifact_prefix}/{training_input.candidate_model_version}" # ex: feedback/model-artifacts/bge-m3-...KST.
        cloned_artifact_refs = await self._clone_active_serving_model(training_input)
        local_root = workspace_dir / "model_artifacts" / training_input.candidate_model_version
        local_root.mkdir(parents=True, exist_ok=True)
        manifest = self._build_manifest(training_input, cloned_artifact_refs)
        manifest_path = local_root / "model_manifest.json"
        metadata_path = local_root / "training_metadata.json"
        scoring_artifact_path = local_root / "scoring_artifact.json"
        manifest_path.write_text(manifest.to_json(), encoding="utf-8")
        metadata_path.write_text(
            json.dumps(
                _training_metadata(training_input, cloned_artifact_refs),
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ),
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

    def _build_manifest(
        self,
        training_input: TrainingInput,
        cloned_artifact_refs: ClonedModelArtifactRefs | None,
    ) -> ModelArtifactManifest:
        return ModelArtifactManifest(
            candidate_model_version=training_input.candidate_model_version,
            baseline_model_version=training_input.baseline_model_version,
            dataset_version=training_input.dataset_version,
            evaluation_dataset_ref=training_input.evaluation_dataset_ref,
            training_config_hash=training_input.training_config_hash,
            base_model_name=self._base_model_name,
            embedding_dimension=self._embedding_dimension,
            artifact_format=self._artifact_format,
            created_at=training_input.started_at,
            # 복제했을 때만 출처 정보를 채운다. 안 했으면 manifest 기본값(None)이 들어간다. ** 는 a:b 딕셔너리를 a=b 형태로 품
            **_clone_provenance_fields(cloned_artifact_refs),
        )

    async def _clone_active_serving_model(self, training_input: TrainingInput) -> ClonedModelArtifactRefs | None:
        if self._clone_active_model is None:
            return None
        return await self._clone_active_model.clone(
            active_model_version=training_input.baseline_model_version,
            candidate_model_version=training_input.candidate_model_version,
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
            for positive_tokens in _candidate_token_sets(row, "positives"):
                for token in query_tokens & positive_tokens:
                    weights[token] = weights.get(token, 1.0) + 1.0
            for negative_tokens in _candidate_token_sets(row, "negatives"):
                for token in query_tokens & negative_tokens:
                    weights[token] = max(0.1, weights.get(token, 1.0) - 0.25)
        return {token: weights[token] for token in sorted(weights)}


def _candidate_token_sets(row: dict[str, object], field_name: str) -> tuple[set[str], ...]:
    candidates = row.get(field_name, [])
    if not isinstance(candidates, list):
        return ()
    candidate_token_sets: list[set[str]] = []
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate_token_sets.append(set(tokens(str(candidate.get("text", "")))))
    return tuple(candidate_token_sets)


def _storage_path_from_ref(artifact_ref: str) -> str:
    if not artifact_ref.startswith("gs://"):
        return artifact_ref
    bucket_and_path = artifact_ref.removeprefix("gs://")
    _, separator, storage_path = bucket_and_path.partition("/")
    if not separator or not storage_path:
        raise ValueError(f"Invalid artifact ref: {artifact_ref}")
    return storage_path


def _clone_provenance_fields(
    cloned_artifact_refs: ClonedModelArtifactRefs | None,
) -> dict[str, str]:
    # 복제 출처 정보 5개를 한곳에서 만든다(manifest와 metadata가 공유).
    # 복제 안 했으면 빈 dict라 호출 측에서 기본값을 쓰게 된다.
    if cloned_artifact_refs is None:
        return {}
    return {
        "artifact_source_type": "cloned_active_model",
        "source_model_version": cloned_artifact_refs.source_model_version,
        "source_artifact_ref": cloned_artifact_refs.source_artifact_ref,
        "candidate_artifact_ref": cloned_artifact_refs.candidate_artifact_ref,
        "serving_artifact_ref": cloned_artifact_refs.serving_artifact_ref,
    }


def _training_metadata(
    training_input: TrainingInput,
    cloned_artifact_refs: ClonedModelArtifactRefs | None,
) -> dict[str, object]:
    metadata: dict[str, object] = asdict(training_input) #asdict: dataclass(데이터 클래스) 객체를 딕셔너리로 변환 {필드명: 값} => json으로 저장 목적
    metadata.update(_clone_provenance_fields(cloned_artifact_refs))
    return metadata
