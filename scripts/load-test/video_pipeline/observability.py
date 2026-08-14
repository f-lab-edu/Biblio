from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from infrastructure import CommandRunner, LoadTestError


_LOG_CONTEXT = re.compile(
    r"trace_id=(?P<trace_id>\S+) video_id=(?P<video_id>\S+) "
    r"user_id=\S+ \| (?P<message>.*)$"
)
_FIELD = re.compile(r"(?P<name>[a-z_]+)=(?P<value>\S+)")


@dataclass(frozen=True)
class WorkerLogDatasets:
    stage_events: tuple[dict[str, Any], ...]
    pipeline_timings: tuple[dict[str, Any], ...]
    queue_samples: tuple[dict[str, Any], ...]
    worker_process_samples: tuple[dict[str, Any], ...]


def collect_worker_logs(
    commands: CommandRunner,
    *,
    project_id: str,
    start_time: str,
    end_time: str,
    service_name: str = "pipeline-worker",
) -> WorkerLogDatasets:
    filter_expression = (
        'resource.type="cloud_run_revision" '
        f'resource.labels.service_name="{service_name}" '
        f'timestamp>="{start_time}" timestamp<="{end_time}"'
    )
    raw_output = commands.output(
        [
            "gcloud",
            "logging",
            "read",
            filter_expression,
            "--project",
            project_id,
            "--format=json",
            "--order=asc",
        ]
    )
    try:
        entries = json.loads(raw_output or "[]")
    except json.JSONDecodeError as error:
        raise LoadTestError("Could not decode pipeline-worker Cloud Logging output.") from error
    if not isinstance(entries, list):
        raise LoadTestError("pipeline-worker Cloud Logging output must be a JSON list.")
    return parse_worker_logs(entries)


def parse_worker_logs(entries: list[object]) -> WorkerLogDatasets:
    stage_events: list[dict[str, Any]] = []
    pipeline_timings: list[dict[str, Any]] = []
    queue_samples: list[dict[str, Any]] = []
    worker_process_samples: list[dict[str, Any]] = []
    for entry in entries:
        parsed = _parse_entry(entry)
        if parsed is None:
            continue
        message = str(parsed.pop("message"))
        fields = _message_fields(message)
        record = {**parsed, **fields}
        if message.startswith("pipeline.stage "):
            stage_events.append(record)
        elif message.startswith("pipeline.timing "):
            pipeline_timings.append(record)
        elif message.startswith("queue.sample "):
            queue_samples.append(record)
        elif message.startswith("worker.process.sample "):
            worker_process_samples.append(record)
    return WorkerLogDatasets(
        stage_events=tuple(stage_events),
        pipeline_timings=tuple(pipeline_timings),
        queue_samples=tuple(queue_samples),
        worker_process_samples=tuple(worker_process_samples),
    )


def write_worker_log_datasets(
    result_directory: Path,
    datasets: WorkerLogDatasets,
) -> None:
    _write_json_lines(result_directory / "stage-events.jsonl", datasets.stage_events)
    _write_json_lines(
        result_directory / "pipeline-timings.jsonl",
        datasets.pipeline_timings,
    )
    _write_csv(result_directory / "queue-samples.csv", datasets.queue_samples)
    _write_csv(
        result_directory / "resource-samples" / "worker-process.csv",
        datasets.worker_process_samples,
    )


def _parse_entry(entry: object) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    payload = entry.get("textPayload")
    timestamp = entry.get("timestamp")
    if not isinstance(payload, str) or not isinstance(timestamp, str):
        return None
    match = _LOG_CONTEXT.search(payload)
    if match is None:
        return None
    return {
        "log_timestamp_utc": timestamp,
        "trace_id": match.group("trace_id"),
        "video_id": match.group("video_id"),
        "message": match.group("message"),
    }


def _message_fields(message: str) -> dict[str, Any]:
    return {
        match.group("name"): _coerce_value(match.group("value"))
        for match in _FIELD.finditer(message)
    }


def _coerce_value(value: str) -> str | int | float:
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _write_json_lines(path: Path, rows: tuple[dict[str, Any], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text("\n".join(serialized) + ("\n" if rows else ""), encoding="utf-8")


def _write_csv(path: Path, rows: tuple[dict[str, Any], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = tuple(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        if not fieldnames:
            return
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
