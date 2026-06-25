from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


REQUIRED_MODEL_MANIFEST_FIELDS = frozenset(
    {
        "candidate_model_version",
        "baseline_model_version",
        "dataset_version",
        "evaluation_dataset_ref",
        "training_config_hash",
        "base_model_name",
        "embedding_dimension",
        "artifact_format",
        "created_at",
    }
)


def _format_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class ModelArtifactManifest:
    candidate_model_version: str
    baseline_model_version: str
    dataset_version: str
    evaluation_dataset_ref: str
    training_config_hash: str
    base_model_name: str
    embedding_dimension: int
    artifact_format: str
    created_at: datetime
    artifact_source_type: str | None = None
    source_model_version: str | None = None
    source_artifact_ref: str | None = None
    candidate_artifact_ref: str | None = None
    serving_artifact_ref: str | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["created_at"] = _format_datetime(self.created_at)
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str | bytes) -> ModelArtifactManifest:
        return cls.from_dict(json.loads(payload))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ModelArtifactManifest:
        missing_fields = sorted(REQUIRED_MODEL_MANIFEST_FIELDS - payload.keys())
        if missing_fields:
            raise ValueError(f"missing model manifest field: {missing_fields[0]}")
        return cls(
            candidate_model_version=str(payload["candidate_model_version"]),
            baseline_model_version=str(payload["baseline_model_version"]),
            dataset_version=str(payload["dataset_version"]),
            evaluation_dataset_ref=str(payload["evaluation_dataset_ref"]),
            training_config_hash=str(payload["training_config_hash"]),
            base_model_name=str(payload["base_model_name"]),
            embedding_dimension=int(payload["embedding_dimension"]),
            artifact_format=str(payload["artifact_format"]),
            created_at=_parse_datetime(str(payload["created_at"])),
            artifact_source_type=_optional_str(payload.get("artifact_source_type")),
            source_model_version=_optional_str(payload.get("source_model_version")),
            source_artifact_ref=_optional_str(payload.get("source_artifact_ref")),
            candidate_artifact_ref=_optional_str(payload.get("candidate_artifact_ref")),
            serving_artifact_ref=_optional_str(payload.get("serving_artifact_ref")),
        )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
