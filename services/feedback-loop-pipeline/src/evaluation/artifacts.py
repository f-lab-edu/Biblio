from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID

from src.evaluation.evaluator import EvaluationResult
from src.infra.storage.client import ArtifactStore


@dataclass(frozen=True)
class EvaluationDetailArtifactRefs:
    evaluation_id: UUID
    storage_path: str
    uri: str


class EvaluationDetailArtifactWriter:
    def __init__(self, *, artifact_store: ArtifactStore, evaluation_artifact_prefix: str) -> None:
        self._artifact_store = artifact_store
        self._evaluation_artifact_prefix = evaluation_artifact_prefix.rstrip("/")

    async def write_details(
        self,
        evaluation_id: UUID,
        result: EvaluationResult,
        *,
        workspace_dir: Path,
    ) -> EvaluationDetailArtifactRefs:
        storage_path = f"{self._evaluation_artifact_prefix}/{evaluation_id}/details.jsonl"
        local_dir = workspace_dir / "evaluation_artifacts" / str(evaluation_id)
        local_dir.mkdir(parents=True, exist_ok=True)
        detail_path = local_dir / "details.jsonl"
        lines = [
            json.dumps(asdict(detail), sort_keys=True, separators=(",", ":"))
            for detail in result.details
        ]
        detail_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        await self._artifact_store.upload_object(detail_path, storage_path)
        return EvaluationDetailArtifactRefs(
            evaluation_id=evaluation_id,
            storage_path=storage_path,
            uri=self._artifact_store.object_uri(storage_path),
        )
