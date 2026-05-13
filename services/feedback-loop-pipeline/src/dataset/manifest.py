from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from src.infra.storage.client import ArtifactStore


GENERATION_RULE_VERSION = "triplet-v1"


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class DatasetManifest:
    dataset_version: str
    created_at: datetime
    generation_rule_version: str
    source_window_start: datetime
    source_window_end: datetime
    input_event_count: int
    deduped_event_count: int
    trainable_event_count: int
    triplet_row_count: int

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        for key in ("created_at", "source_window_start", "source_window_end"):
            data[key] = _format_datetime(data[key])
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str | bytes) -> DatasetManifest:
        raw = json.loads(payload)
        return cls(
            dataset_version=str(raw["dataset_version"]),
            created_at=_parse_datetime(str(raw["created_at"])),
            generation_rule_version=str(raw["generation_rule_version"]),
            source_window_start=_parse_datetime(str(raw["source_window_start"])),
            source_window_end=_parse_datetime(str(raw["source_window_end"])),
            input_event_count=int(raw["input_event_count"]),
            deduped_event_count=int(raw["deduped_event_count"]),
            trainable_event_count=int(raw["trainable_event_count"]),
            triplet_row_count=int(raw["triplet_row_count"]),
        )


@dataclass(frozen=True)
class DatasetEligibilityPolicy:
    min_deduped_event_count: int = 1
    min_triplet_row_count: int = 1
    supported_generation_rule_versions: frozenset[str] = frozenset({GENERATION_RULE_VERSION})


def is_manifest_eligible(manifest: DatasetManifest, policy: DatasetEligibilityPolicy) -> bool:
    return (
        manifest.generation_rule_version in policy.supported_generation_rule_versions
        and manifest.trainable_event_count > 0
        and manifest.deduped_event_count >= policy.min_deduped_event_count
        and manifest.triplet_row_count >= policy.min_triplet_row_count
    )


class DatasetManifestSelector:
    def __init__(self, artifact_store: ArtifactStore, policy: DatasetEligibilityPolicy | None = None) -> None:
        self._artifact_store = artifact_store
        self._policy = policy or DatasetEligibilityPolicy()

    async def select_latest_eligible(self, artifact_prefix: str, *, workspace_dir: Path) -> DatasetManifest | None:
        manifest_paths = sorted(
            path
            for path in await self._artifact_store.list_objects(artifact_prefix.rstrip("/") + "/")
            if path.endswith("/manifest.json")
        )
        manifest_dir = workspace_dir / "dataset_manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)

        downloaded = await asyncio.gather(*[
            self._download_manifest(index, path, manifest_dir)
            for index, path in enumerate(manifest_paths)
        ])
        candidates = [
            manifest
            for manifest in downloaded
            if manifest is not None and is_manifest_eligible(manifest, self._policy)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda manifest: (manifest.created_at, manifest.dataset_version))

    async def _download_manifest(self, index: int, storage_path: str, manifest_dir: Path) -> DatasetManifest | None:
        destination = manifest_dir / f"{index}.json"
        await self._artifact_store.download_object(storage_path, destination)
        return self._safe_load_manifest(destination)

    @staticmethod
    def _safe_load_manifest(path: Path) -> DatasetManifest | None:
        try:
            return DatasetManifest.from_json(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
