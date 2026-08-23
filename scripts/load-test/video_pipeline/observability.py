from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from infrastructure import CommandRunner, LoadTestError


_LOG_CONTEXT = re.compile(
    r"trace_id=(?P<trace_id>\S+) video_id=(?P<video_id>\S+) "
    r"user_id=\S+ \| (?P<message>.*)$"
)
_FIELD = re.compile(r"(?P<name>[a-z_]+)=(?P<value>\S+)")
_EVENT_PREFIXES = (
    "queue.message.started",
    "embedding.request.",
    "stt.request.",
    "enrichment.step",
)


@dataclass(frozen=True)
class WorkerLogDatasets:
    events: tuple[dict[str, Any], ...]
    pipeline_timings: tuple[dict[str, Any], ...]
    samples: tuple[dict[str, Any], ...]
    raw_entries: tuple[object, ...]

    @property
    def stage_events(self) -> tuple[dict[str, Any], ...]:
        stage_events = []
        for row in self.events:
            event_type = str(row.get("event_type", ""))
            if event_type.startswith("pipeline.stage."):
                stage_events.append(
                    {**row, "event": event_type.rsplit(".", 1)[-1]}
                )
            elif event_type.startswith("pipeline.work."):
                lifecycle_event = event_type.rsplit(".", 1)[-1]
                if lifecycle_event not in {
                    "started",
                    "succeeded",
                    "retryable_failed",
                    "failed",
                    "skipped",
                }:
                    continue
                stage_events.append(
                    {
                        **row,
                        "event": (
                            "started"
                            if lifecycle_event == "started"
                            else "finished"
                        ),
                        "status": lifecycle_event,
                    }
                )
        return tuple(stage_events)

    @property
    def queue_samples(self) -> tuple[dict[str, Any], ...]:
        return tuple(row for row in self.samples if row.get("source") == "pgmq")

    @property
    def worker_process_samples(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            row for row in self.samples if row.get("source") == "worker-process"
        )


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
    events: list[dict[str, Any]] = []
    pipeline_timings: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    stt_starts: dict[str, deque[datetime]] = defaultdict(deque)
    for entry in entries:
        parsed = _parse_entry(entry)
        if parsed is None:
            continue
        message = str(parsed.pop("message"))
        structured_event_name = parsed.pop("event_name", None)
        if structured_event_name is not None:
            record = {**parsed, "message": message}
            event_name = str(structured_event_name)
            if event_name in {"queue.sample", "stage.work.sample", "worker.process.sample"}:
                samples.append(_structured_sample(record, event_name))
            else:
                events.append(_as_event(record, event_name))
            continue
        fields = _message_fields(message)
        record = {**parsed, **fields}
        if message.startswith("pipeline.stage "):
            stage_event = str(record.pop("event", "unknown"))
            events.append(_as_event(record, f"pipeline.stage.{stage_event}"))
        elif message.startswith("pipeline.timing "):
            pipeline_timings.append(record)
        elif message.startswith("queue.sample "):
            samples.append(_as_sample(record, "pgmq"))
        elif message.startswith("worker.process.sample "):
            samples.append(_as_sample(record, "worker-process"))
        elif message.startswith("STT BatchRecognize start "):
            event = _as_event(record, "stt.request.started")
            events.append(event)
            stt_starts[str(record.get("trace_id", ""))].append(_row_timestamp(event))
        elif message.startswith("STT BatchRecognize done "):
            event = _as_event(record, "stt.request.succeeded")
            trace_id = str(record.get("trace_id", ""))
            if stt_starts[trace_id]:
                event["duration_ms"] = (
                    _row_timestamp(event) - stt_starts[trace_id].popleft()
                ).total_seconds() * 1000
            events.append(event)
        elif message.startswith("event=embedding.request."):
            event_type = str(record.pop("event", "embedding.request.unknown"))
            events.append(_as_event(record, event_type))
        elif message.startswith(_EVENT_PREFIXES):
            events.append(_as_event(record, message.split(" ", 1)[0]))
    return WorkerLogDatasets(
        events=tuple(events),
        pipeline_timings=tuple(pipeline_timings),
        samples=tuple(samples),
        raw_entries=tuple(entries),
    )


def parse_embedding_endpoint_log(
    path: Path,
    *,
    trace_ids: set[str],
) -> tuple[dict[str, Any], ...]:
    if not path.is_file():
        return ()
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("trace_id") not in trace_ids:
            continue
        event_type = payload.pop("msg", None)
        timestamp = payload.pop("ts", None)
        if not isinstance(event_type, str) or not isinstance(timestamp, str):
            continue
        events.append(
            {
                "timestamp_utc": timestamp,
                "source": "embedding-vm",
                "event_type": event_type,
                **payload,
            }
        )
    return tuple(events)


def select_run_traces(
    datasets: WorkerLogDatasets,
    trace_ids: set[str],
) -> WorkerLogDatasets:
    return WorkerLogDatasets(
        events=tuple(
            row for row in datasets.events if row.get("trace_id") in trace_ids
        ),
        pipeline_timings=tuple(
            row
            for row in datasets.pipeline_timings
            if row.get("trace_id") in trace_ids
        ),
        samples=datasets.samples,
        raw_entries=datasets.raw_entries,
    )


def _as_event(record: dict[str, Any], event_type: str) -> dict[str, Any]:
    timestamp = record.pop("timestamp_utc", record.get("log_timestamp_utc"))
    return {
        "timestamp_utc": timestamp,
        "source": "pipeline-worker",
        "event_type": event_type,
        **record,
    }


def _as_sample(record: dict[str, Any], source: str) -> dict[str, Any]:
    timestamp = record.pop("timestamp_utc", record.get("log_timestamp_utc"))
    return {"timestamp_utc": timestamp, "source": source, **record}


def _parse_entry(entry: object) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    json_payload = entry.get("jsonPayload")
    if isinstance(json_payload, dict):
        message = json_payload.get("message")
        if not isinstance(message, str):
            return None
        return {
            "log_timestamp_utc": entry.get("timestamp"),
            **json_payload,
            "message": message,
        }
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


def _structured_sample(record: dict[str, Any], event_name: str) -> dict[str, Any]:
    source = {
        "queue.sample": "pgmq",
        "stage.work.sample": "pipeline-db",
        "worker.process.sample": "worker-process",
    }[event_name]
    return _as_sample(record, source)


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


def _row_timestamp(row: dict[str, Any]) -> datetime:
    raw_timestamp = row.get("timestamp_utc")
    if not isinstance(raw_timestamp, str):
        raise LoadTestError(f"Event is missing timestamp_utc: {row!r}")
    return datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
