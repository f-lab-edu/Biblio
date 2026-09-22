from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from infrastructure import LoadTestError
from video_pipeline.models import DispatchPhase, FixtureKind, FixtureSpec


FIXTURE_KINDS: tuple[FixtureKind, ...] = ("short", "medium", "long")


def load_fixture_manifest(path: Path) -> dict[FixtureKind, FixtureSpec]:
    payload = _read_manifest(path)
    fixtures = payload.get("fixtures", payload)
    if not isinstance(fixtures, dict):
        raise LoadTestError("Fixture manifest must contain a JSON object.")
    return {
        kind: _fixture_spec(kind, fixtures.get(kind), path.parent)
        for kind in FIXTURE_KINDS
    }


def fixture_workload(
    phases: tuple[DispatchPhase, ...],
    repeat_count: int,
    fixtures: dict[FixtureKind, FixtureSpec],
) -> dict[str, Any]:
    request_counts = {kind: 0 for kind in FIXTURE_KINDS}
    for phase in phases:
        request_counts[phase.fixture] += phase.request_count * repeat_count
    total_duration_seconds = sum(
        request_counts[kind] * fixtures[kind].duration_seconds
        for kind in FIXTURE_KINDS
    )
    return {
        "request_counts": request_counts,
        "total_requests": sum(request_counts.values()),
        "total_fixture_duration_seconds": total_duration_seconds,
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LoadTestError(f"Invalid fixture manifest: {path}") from error
    if not isinstance(payload, dict):
        raise LoadTestError("Fixture manifest must contain a JSON object.")
    return payload


def _fixture_spec(
    kind: FixtureKind,
    raw_fixture: object,
    manifest_directory: Path,
) -> FixtureSpec:
    if not isinstance(raw_fixture, dict):
        raise LoadTestError(f"Fixture manifest is missing {kind!r}.")
    fixture_path = _fixture_path(raw_fixture, manifest_directory, kind)
    expected_sha256 = _required_string(raw_fixture, "sha256", kind).lower()
    expected_size = _positive_int(raw_fixture, "size_bytes", kind)
    duration_seconds = _positive_number(raw_fixture, "duration_seconds", kind)
    actual_size = fixture_path.stat().st_size
    if actual_size != expected_size:
        raise LoadTestError(
            f"Fixture {kind!r} size mismatch: expected {expected_size}, got {actual_size}."
        )
    actual_sha256 = _sha256(fixture_path)
    if actual_sha256 != expected_sha256:
        raise LoadTestError(
            f"Fixture {kind!r} SHA-256 mismatch: expected {expected_sha256}, "
            f"got {actual_sha256}."
        )
    return FixtureSpec(
        kind=kind,
        path=fixture_path,
        sha256=actual_sha256,
        duration_seconds=duration_seconds,
        size_bytes=actual_size,
    )


def _fixture_path(
    raw_fixture: dict[str, object],
    manifest_directory: Path,
    kind: FixtureKind,
) -> Path:
    raw_path = _required_string(raw_fixture, "path", kind)
    path = Path(raw_path)
    resolved = path if path.is_absolute() else manifest_directory / path
    resolved = resolved.resolve()
    if not resolved.is_file():
        raise LoadTestError(f"Fixture {kind!r} does not exist: {resolved}")
    return resolved


def _required_string(
    fixture: dict[str, object],
    field: str,
    kind: FixtureKind,
) -> str:
    value = fixture.get(field)
    if not isinstance(value, str) or not value:
        raise LoadTestError(f"Fixture {kind!r} requires a non-empty {field}.")
    return value


def _positive_int(
    fixture: dict[str, object],
    field: str,
    kind: FixtureKind,
) -> int:
    value = fixture.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise LoadTestError(f"Fixture {kind!r} requires a positive {field}.")
    return value


def _positive_number(
    fixture: dict[str, object],
    field: str,
    kind: FixtureKind,
) -> float:
    value = fixture.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise LoadTestError(f"Fixture {kind!r} requires a positive {field}.")
    return float(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fixture_file:
        for chunk in iter(lambda: fixture_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
