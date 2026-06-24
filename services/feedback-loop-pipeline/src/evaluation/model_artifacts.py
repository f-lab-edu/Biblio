from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.infra.storage.client import ArtifactStore


@dataclass(frozen=True)
class LocalScoringModelArtifact:
    term_weights: dict[str, float]


class LocalScoringModelArtifactLoader:
    def __init__(self, *, artifact_store: ArtifactStore, model_artifact_prefix: str) -> None:
        self._artifact_store = artifact_store
        self._model_artifact_prefix = model_artifact_prefix.rstrip("/")

    async def load(
        self,
        model_version: str,
        *,
        workspace_dir: Path,
    ) -> LocalScoringModelArtifact:
        storage_path = f"{self._model_artifact_prefix}/{model_version}/scoring_artifact.json"
        destination = workspace_dir / "model_artifacts" / model_version / "scoring_artifact.json"
        try:
            await self._artifact_store.download_object(storage_path, destination)
        except FileNotFoundError:
            return LocalScoringModelArtifact(term_weights={})
        payload = json.loads(destination.read_text(encoding="utf-8"))
        return LocalScoringModelArtifact(
            term_weights={
                str(token): float(weight)
                for token, weight in dict(payload.get("term_weights", {})).items()
            }
        )
