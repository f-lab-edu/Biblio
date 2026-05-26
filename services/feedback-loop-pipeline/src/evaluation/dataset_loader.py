from __future__ import annotations

import json
from pathlib import Path

from src.evaluation.evaluator import (
    EvaluationCorpusRow,
    EvaluationDataset,
    EvaluationQuery,
)
from src.infra.storage.client import ArtifactStore


class EvaluationDatasetLoader:
    def __init__(self, artifact_store: ArtifactStore) -> None:
        self._artifact_store = artifact_store

    async def load(self, evaluation_dataset_ref: str, *, workspace_dir: Path) -> EvaluationDataset:
        storage_path = _storage_path_from_ref(evaluation_dataset_ref)
        destination = workspace_dir / "evaluation_dataset" / Path(storage_path).name
        await self._artifact_store.download_object(storage_path, destination)
        payload = json.loads(destination.read_text(encoding="utf-8"))
        return EvaluationDataset(
            evaluation_dataset_ref=evaluation_dataset_ref,
            queries=[
                EvaluationQuery(
                    query_text=str(row["query_text"]),
                    relevant_chunk_ids=[str(chunk_id) for chunk_id in row["relevant_chunk_ids"]],
                )
                for row in payload["queries"]
            ],
            corpus=[
                EvaluationCorpusRow(
                    chunk_id=str(row["chunk_id"]),
                    chunk_text=str(row["chunk_text"]),
                )
                for row in payload["corpus"]
            ],
        )


def _storage_path_from_ref(evaluation_dataset_ref: str) -> str:
    if not evaluation_dataset_ref.startswith("gs://"):
        return evaluation_dataset_ref
    bucket_and_path = evaluation_dataset_ref.removeprefix("gs://")
    _, separator, storage_path = bucket_and_path.partition("/")
    if not separator or not storage_path:
        raise ValueError(f"Invalid evaluation dataset ref: {evaluation_dataset_ref}")
    return storage_path
