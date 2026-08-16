from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from video_pipeline.models import CompleteRequestRecord, TerminalStatusRecord


def write_video_pipeline_artifacts(
    result_directory: Path,
    *,
    run_metadata: dict[str, Any],
    fixture_manifest: dict[str, Any],
    requests: tuple[CompleteRequestRecord, ...],
    terminal_statuses: tuple[TerminalStatusRecord, ...],
    pipeline_timings: tuple[dict[str, Any], ...] = (),
    events: tuple[dict[str, Any], ...] = (),
    samples: tuple[dict[str, Any], ...] = (),
    errors: tuple[str, ...] = (),
) -> None:
    result_directory.mkdir(parents=True, exist_ok=True)
    _write_json(result_directory / "environment.json", run_metadata)
    _write_json(result_directory / "fixtures.json", fixture_manifest)
    _write_json_lines(
        result_directory / "video-results.jsonl",
        _video_result_rows(requests, terminal_statuses, pipeline_timings),
    )
    all_events = (*events, *_error_events(errors, run_metadata))
    _write_json_lines(result_directory / "events.jsonl", all_events)
    _write_json_lines(result_directory / "samples.jsonl", samples)


def write_raw_log_archive(
    result_directory: Path,
    *,
    worker_entries: tuple[object, ...] = (),
    endpoint_log_path: Path | None = None,
    sampler_log_path: Path | None = None,
) -> None:
    sources: list[tuple[Path, str]] = []
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        if worker_entries:
            worker_path = temporary_root / "pipeline-worker.jsonl"
            _write_json_lines(worker_path, worker_entries)
            sources.append((worker_path, worker_path.name))
        for source_path, archive_name in (
            (endpoint_log_path, "embedding-endpoint.log"),
            (sampler_log_path, "embedding-vm-sampler.log"),
        ):
            if source_path is not None and source_path.is_file():
                sources.append((source_path, archive_name))
        if not sources:
            return
        archive_path = result_directory / "raw-logs.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            for source_path, archive_name in sources:
                archive.add(source_path, arcname=archive_name)


def remove_intermediate_artifacts(result_directory: Path) -> None:
    for directory_name in ("resource-samples", "target-vm"):
        directory = result_directory / directory_name
        if directory.is_dir():
            shutil.rmtree(directory)


def _video_result_rows(
    requests: tuple[CompleteRequestRecord, ...],
    terminal_statuses: tuple[TerminalStatusRecord, ...],
    pipeline_timings: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    terminal_by_video = {record.video_id: record for record in terminal_statuses}
    timing_by_trace = {
        str(record.get("trace_id")): record
        for record in pipeline_timings
        if record.get("trace_id")
    }
    rows = []
    for request in requests:
        terminal = terminal_by_video.get(request.video_id)
        timing = timing_by_trace.get(request.trace_id, {})
        rows.append(
            {
                "video_id": request.video_id,
                "fixture": request.fixture,
                "trace_id": request.trace_id,
                "complete_started_at": request.started_at.isoformat(),
                "complete_responded_at": request.responded_at.isoformat(),
                "response_status": request.response_status,
                "request_error": request.error,
                "terminal_status": terminal.status if terminal else None,
                "terminal_observed_at": (
                    terminal.observed_at.isoformat() if terminal else None
                ),
                "pipeline_timing": _pipeline_timing(timing),
            }
        )
    return tuple(rows)


def _pipeline_timing(record: dict[str, Any]) -> dict[str, Any] | None:
    if not record:
        return None
    excluded = {"log_timestamp_utc", "timestamp_utc", "trace_id", "video_id"}
    return {key: value for key, value in record.items() if key not in excluded}


def _error_events(
    errors: tuple[str, ...],
    run_metadata: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    timestamp = run_metadata.get("finished_at")
    return tuple(
        {
            "timestamp_utc": timestamp,
            "source": "load-test-driver",
            "event_type": "driver.error",
            "status": "failed",
            "error": error,
        }
        for error in errors
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _write_json_lines(path: Path, records: tuple[Any, ...]) -> None:
    lines = (
        json.dumps(
            asdict(record) if is_dataclass(record) else record,
            ensure_ascii=False,
            default=_json_default,
        )
        for record in records
    )
    path.write_text("\n".join(lines) + ("\n" if records else ""), encoding="utf-8")


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__} to JSON.")
