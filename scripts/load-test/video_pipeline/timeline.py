from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from infrastructure import LoadTestError


STAGES = ("download", "audio", "stt", "chunk_enrichment", "embedding", "persist")


@dataclass(frozen=True)
class StageInterval:
    video_id: str
    stage: str
    started_at: datetime
    finished_at: datetime


def build_timeline(
    *,
    stage_events: tuple[dict[str, Any], ...],
    queue_samples: tuple[dict[str, Any], ...],
    resource_samples: tuple[dict[str, Any], ...],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    intervals = _stage_intervals(stage_events)
    ordered_queue_samples = sorted(queue_samples, key=_row_timestamp)
    ordered_resources = sorted(resource_samples, key=_row_timestamp)
    coverage = _cloud_monitoring_coverage(intervals, ordered_resources)
    rows = [
        _timeline_row(sample, intervals, ordered_queue_samples, coverage)
        for sample in ordered_resources
    ]
    return rows, coverage


def write_timeline_artifacts(
    result_directory: Path,
    rows: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> None:
    _write_csv(result_directory / "timeline.csv", rows)
    (result_directory / "resource-coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_csv_samples(
    path: Path,
    *,
    source: str,
    delimiter: str = ",",
) -> tuple[dict[str, Any], ...]:
    if not path.is_file():
        return ()
    with path.open(encoding="utf-8", newline="") as csv_file:
        return tuple(
            {**row, "resource_sample_source": source}
            for row in csv.DictReader(csv_file, delimiter=delimiter)
        )


def _stage_intervals(
    stage_events: tuple[dict[str, Any], ...],
) -> tuple[StageInterval, ...]:
    started: dict[tuple[str, str], datetime] = {}
    intervals: list[StageInterval] = []
    for event in sorted(stage_events, key=_row_timestamp):
        video_id = str(event.get("video_id", ""))
        stage = str(event.get("stage", ""))
        event_name = str(event.get("event", ""))
        timestamp = _row_timestamp(event)
        key = (video_id, stage)
        if event_name == "started":
            if key in started:
                raise LoadTestError(
                    f"Stage events contain duplicate started records: {video_id}:{stage}"
                )
            started[key] = timestamp
        elif event_name == "finished":
            if key not in started:
                raise LoadTestError(
                    f"Stage events contain finished without started: {video_id}:{stage}"
                )
            started_at = started.pop(key)
            if timestamp < started_at:
                raise LoadTestError(
                    f"Stage finished before it started: {video_id}:{stage}"
                )
            intervals.append(StageInterval(video_id, stage, started_at, timestamp))
    if started:
        missing = ", ".join(f"{video_id}:{stage}" for video_id, stage in started)
        raise LoadTestError(f"Stage events are missing finished records: {missing}")
    return tuple(intervals)


def _cloud_monitoring_coverage(
    intervals: tuple[StageInterval, ...],
    resources: list[dict[str, Any]],
) -> dict[str, Any]:
    cloud_timestamps = [
        _row_timestamp(row)
        for row in resources
        if row.get("resource_sample_source") == "cloud-monitoring"
    ]
    process_timestamps = [
        _row_timestamp(row)
        for row in resources
        if row.get("resource_sample_source") == "worker-process"
    ]
    stage_coverage = []
    for interval in intervals:
        sample_count = sum(
            interval.started_at <= timestamp <= interval.finished_at
            for timestamp in cloud_timestamps
        )
        process_sample_count = sum(
            interval.started_at <= timestamp <= interval.finished_at
            for timestamp in process_timestamps
        )
        fallback_required = sample_count < 3
        stage_coverage.append(
            {
                "video_id": interval.video_id,
                "stage": interval.stage,
                "cloud_monitoring_sample_count": sample_count,
                "worker_process_sample_count": process_sample_count,
                "worker_process_fallback_required": fallback_required,
                "resource_data_sufficient": (
                    process_sample_count >= 3 if fallback_required else True
                ),
            }
        )
    return {
        "minimum_cloud_samples_per_stage": 3,
        "stages": stage_coverage,
    }


def _timeline_row(
    sample: dict[str, Any],
    intervals: tuple[StageInterval, ...],
    queue_samples: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    timestamp = _row_timestamp(sample)
    active = [
        interval
        for interval in intervals
        if interval.started_at <= timestamp <= interval.finished_at
    ]
    stage_counts = Counter(interval.stage for interval in active)
    queue_sample = _latest_queue_sample(queue_samples, timestamp)
    active_video_ids = sorted({interval.video_id for interval in active})
    if sum(stage_counts.values()) != len(active_video_ids):
        raise LoadTestError(
            "A video has overlapping pipeline stages at "
            f"{timestamp.isoformat()}."
        )
    return {
        "timestamp_utc": timestamp.isoformat(),
        "resource_sample_source": sample.get("resource_sample_source", "unknown"),
        "active_video_count": len(active_video_ids),
        **{f"{stage}_active_count": stage_counts[stage] for stage in STAGES},
        "queue_ready_count": queue_sample.get("ready", queue_sample.get("ready_count", "")),
        "queue_invisible_count": queue_sample.get(
            "invisible", queue_sample.get("invisible_count", "")
        ),
        "queue_oldest_message_age_seconds": queue_sample.get(
            "oldest_age_sec",
            queue_sample.get("oldest_message_age_seconds", ""),
        ),
        "queue_sample_timestamp_utc": queue_sample.get(
            "timestamp_utc",
            queue_sample.get("log_timestamp_utc", ""),
        ),
        **{
            key: value
            for key, value in sample.items()
            if key not in {"timestamp_utc", "log_timestamp_utc", "resource_sample_source"}
        },
        "worker_process_fallback_required": _fallback_required(
            active,
            coverage,
        ),
    }


def _fallback_required(
    active: list[StageInterval],
    coverage: dict[str, Any],
) -> bool:
    required = {
        (str(row["video_id"]), str(row["stage"]))
        for row in coverage["stages"]
        if row["worker_process_fallback_required"]
    }
    return any((interval.video_id, interval.stage) in required for interval in active)


def _latest_queue_sample(
    queue_samples: list[dict[str, Any]],
    timestamp: datetime,
) -> dict[str, Any]:
    candidates = [row for row in queue_samples if _row_timestamp(row) <= timestamp]
    return candidates[-1] if candidates else {}


def _row_timestamp(row: dict[str, Any]) -> datetime:
    raw_timestamp = row.get("timestamp_utc", row.get("log_timestamp_utc"))
    if not isinstance(raw_timestamp, str) or not raw_timestamp:
        raise LoadTestError(f"Sample is missing timestamp_utc: {row!r}")
    try:
        return datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise LoadTestError(f"Invalid UTC timestamp: {raw_timestamp!r}") from error


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = tuple(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        if not fieldnames:
            return
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
