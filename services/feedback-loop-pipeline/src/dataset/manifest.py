from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from src.infra.storage.client import ArtifactStore


GENERATION_RULE_VERSION = "retrieval-group-v1"


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
    training_group_count: int
    positive_count: int
    negative_count: int
    negative_source_counts: dict[str, int]
    missing_text_drop_count: int
    eligible: bool = False
    ineligible_reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "negative_source_counts", MappingProxyType(dict(self.negative_source_counts)))
        if self.eligible or self.ineligible_reasons:
            return
        reasons = _ineligible_reasons(self, DatasetEligibilityPolicy())
        object.__setattr__(self, "eligible", not reasons)
        object.__setattr__(self, "ineligible_reasons", tuple(reasons))

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_version": self.dataset_version,
            "created_at": _format_datetime(self.created_at),
            "generation_rule_version": self.generation_rule_version,
            "source_window_start": _format_datetime(self.source_window_start),
            "source_window_end": _format_datetime(self.source_window_end),
            "input_event_count": self.input_event_count,
            "deduped_event_count": self.deduped_event_count,
            "trainable_event_count": self.trainable_event_count,
            "training_group_count": self.training_group_count,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "negative_source_counts": dict(self.negative_source_counts),
            "missing_text_drop_count": self.missing_text_drop_count,
            "eligible": self.eligible,
            "ineligible_reasons": list(self.ineligible_reasons),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str | bytes) -> DatasetManifest:
        raw = json.loads(payload)
        negative_source_counts = _parse_negative_source_counts(raw["negative_source_counts"])
        manifest = cls(
            dataset_version=str(raw["dataset_version"]),
            created_at=_parse_datetime(str(raw["created_at"])),
            generation_rule_version=str(raw["generation_rule_version"]),
            source_window_start=_parse_datetime(str(raw["source_window_start"])),
            source_window_end=_parse_datetime(str(raw["source_window_end"])),
            input_event_count=int(raw["input_event_count"]),
            deduped_event_count=int(raw["deduped_event_count"]),
            trainable_event_count=int(raw["trainable_event_count"]),
            training_group_count=int(raw["training_group_count"]),
            positive_count=int(raw["positive_count"]),
            negative_count=int(raw["negative_count"]),
            negative_source_counts=negative_source_counts,
            missing_text_drop_count=int(raw["missing_text_drop_count"]),
            eligible=bool(raw.get("eligible", False)),
            ineligible_reasons=tuple(str(reason) for reason in raw.get("ineligible_reasons", ())),
        )
        return manifest

    def with_eligibility(self, policy: DatasetEligibilityPolicy) -> DatasetManifest:
        reasons = _ineligible_reasons(self, policy)
        return DatasetManifest(
            dataset_version=self.dataset_version,
            created_at=self.created_at,
            generation_rule_version=self.generation_rule_version,
            source_window_start=self.source_window_start,
            source_window_end=self.source_window_end,
            input_event_count=self.input_event_count,
            deduped_event_count=self.deduped_event_count,
            trainable_event_count=self.trainable_event_count,
            training_group_count=self.training_group_count,
            positive_count=self.positive_count,
            negative_count=self.negative_count,
            negative_source_counts=dict(self.negative_source_counts),
            missing_text_drop_count=self.missing_text_drop_count,
            eligible=not reasons,
            ineligible_reasons=tuple(reasons),
        )


@dataclass(frozen=True)
class DatasetEligibilityPolicy:
    min_training_group_count: int = 10
    min_negative_count: int = 20
    supported_generation_rule_versions: frozenset[str] = frozenset({GENERATION_RULE_VERSION})


def is_manifest_eligible(manifest: DatasetManifest, policy: DatasetEligibilityPolicy) -> bool:
    return not _ineligible_reasons(manifest, policy)


def _parse_negative_source_counts(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError("negative_source_counts must be a mapping")
    return {str(source): int(count) for source, count in value.items()}


def _ineligible_reasons(manifest: DatasetManifest, policy: DatasetEligibilityPolicy) -> list[str]:
    reasons: list[str] = []
    if manifest.generation_rule_version not in policy.supported_generation_rule_versions:
        reasons.append("unsupported_generation_rule_version")
    if manifest.training_group_count < policy.min_training_group_count:
        reasons.append("training_group_count_below_minimum")
    if manifest.negative_count < policy.min_negative_count:
        reasons.append("negative_count_below_minimum")
    return reasons


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
