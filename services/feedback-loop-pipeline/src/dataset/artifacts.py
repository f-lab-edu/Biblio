from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.dataset.materializer import MaterializedDataset
from src.infra.storage.client import ArtifactStore


@dataclass(frozen=True)
class DatasetArtifactRefs:
    dataset_version: str
    rows_storage_path: str
    manifest_storage_path: str
    rows_uri: str
    manifest_uri: str


class DatasetArtifactPaths:
    def __init__(self, artifact_prefix: str, dataset_version: str) -> None:
        self._artifact_prefix = artifact_prefix.rstrip("/")
        self._dataset_version = dataset_version

    @property
    def rows_storage_path(self) -> str:
        return f"{self._artifact_prefix}/{self._dataset_version}/train.jsonl"

    @property
    def manifest_storage_path(self) -> str:
        return f"{self._artifact_prefix}/{self._dataset_version}/manifest.json"


class DatasetArtifactWriter:
    def __init__(self, artifact_store: ArtifactStore) -> None:
        self._artifact_store = artifact_store

    async def write_dataset(
        self,
        dataset: MaterializedDataset,
        *,
        artifact_prefix: str,
        workspace_dir: Path,
    ) -> DatasetArtifactRefs:
        paths = DatasetArtifactPaths(artifact_prefix, dataset.manifest.dataset_version)
        local_dir = workspace_dir / "dataset_artifacts" / dataset.manifest.dataset_version
        local_dir.mkdir(parents=True, exist_ok=True)
        rows_path = local_dir / "train.jsonl"
        manifest_path = local_dir / "manifest.json"
        self._write_rows(dataset, rows_path)
        manifest_path.write_text(dataset.manifest.to_json(), encoding="utf-8")

        await self._artifact_store.upload_object(rows_path, paths.rows_storage_path)
        await self._artifact_store.upload_object(manifest_path, paths.manifest_storage_path)
        return DatasetArtifactRefs(
            dataset_version=dataset.manifest.dataset_version,
            rows_storage_path=paths.rows_storage_path,
            manifest_storage_path=paths.manifest_storage_path,
            rows_uri=self._artifact_store.object_uri(paths.rows_storage_path),
            manifest_uri=self._artifact_store.object_uri(paths.manifest_storage_path),
        )

    @staticmethod
    def _write_rows(dataset: MaterializedDataset, destination: Path) -> None:
        lines = [
            json.dumps(row.to_dict(), sort_keys=True, separators=(",", ":"))
            for row in dataset.rows
        ]
        destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
