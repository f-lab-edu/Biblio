from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


LOAD_TEST_DIR = Path(__file__).resolve().parents[1]
if str(LOAD_TEST_DIR) not in sys.path:
    sys.path.insert(0, str(LOAD_TEST_DIR))

from embedding_target import TargetMonitor
from infrastructure import CommandRunner, Infrastructure, Settings
from k6_runner import ArtifactManager
from video_pipeline.artifacts import (
    remove_intermediate_artifacts,
    write_raw_log_archive,
    write_video_pipeline_artifacts,
)
from video_pipeline.models import CompleteRequestRecord, TerminalStatusRecord
from video_pipeline.observability import (
    collect_worker_logs,
    parse_embedding_endpoint_log,
    select_run_traces,
)
from video_pipeline.timeline import build_timeline, write_timeline_artifacts


_LEGACY_FILES = (
    "errors.jsonl",
    "pipeline-timings.jsonl",
    "queue-samples.csv",
    "requests.jsonl",
    "resource-coverage.json",
    "stage-events.jsonl",
    "video-results.csv",
)


def migrate_legacy_run(
    result_directory: Path,
    *,
    commands: CommandRunner | None = None,
    gcp_project_id: str | None = None,
    embedding_runtime_config: dict[str, Any] | None = None,
) -> None:
    environment = _read_json(result_directory / "environment.json")
    requests = _read_requests(result_directory / "requests.jsonl")
    terminal_statuses = _read_terminal_statuses(
        result_directory / "video-results.csv"
    )
    datasets = (
        select_run_traces(
            collect_worker_logs(
                commands,
                project_id=gcp_project_id,
                start_time=str(environment["started_at"]),
                end_time=_timestamp_after(str(environment["finished_at"]), 5.0),
            ),
            {request.trace_id for request in requests},
        )
        if commands is not None and gcp_project_id is not None
        else None
    )
    pipeline_timings = (
        datasets.pipeline_timings
        if datasets is not None
        else _read_json_lines(result_directory / "pipeline-timings.jsonl")
    )
    worker_events = (
        datasets.events if datasets is not None else _legacy_events(result_directory)
    )
    endpoint_events = parse_embedding_endpoint_log(
        result_directory / "target-vm" / "endpoint.log",
        trace_ids={request.trace_id for request in requests},
    )
    events = (*worker_events, *endpoint_events)
    samples = _legacy_samples(
        result_directory,
        include_worker_samples=datasets is None,
    )
    if datasets is not None:
        samples = (*datasets.samples, *samples)
    timeline_rows, coverage = _rebuild_timeline(events, samples)
    observability_errors = _legacy_observability_errors(
        request_count=len(requests),
        timing_count=len(pipeline_timings),
        stage_event_count=sum(
            row.get("event_type", "").startswith("pipeline.stage.") for row in events
        ),
    )
    environment.update(
        {
            "artifact_schema_version": 2,
            "status": "incomplete" if observability_errors else environment.get("status"),
            "workload_status": environment.get("status", "complete"),
            "observability_status": (
                "incomplete" if observability_errors else "complete"
            ),
            "observability_errors": observability_errors,
            "collection": {
                "legacy_migration": True,
                "cloud_logs_recovered": datasets is not None,
                "log_window_grace_seconds": 5.0 if datasets is not None else None,
                "event_count": len(events),
                "sample_count": len(samples),
            },
            "runtime_config": {
                "availability": "unavailable_legacy_not_captured"
            },
            "recovery_context": {
                "embedding_vm_snapshot_at_migration": embedding_runtime_config,
                "warning": "This is not the historical test-time configuration.",
            }
            if embedding_runtime_config is not None
            else {},
            "resource_coverage": coverage,
            "target_vm_summary": _read_json(
                result_directory / "target-vm" / "target-metrics.json"
            ),
        }
    )
    write_video_pipeline_artifacts(
        result_directory,
        run_metadata=environment,
        fixture_manifest=_read_json(result_directory / "fixtures.json"),
        requests=requests,
        terminal_statuses=terminal_statuses,
        pipeline_timings=pipeline_timings,
        events=events,
        samples=samples,
    )
    write_raw_log_archive(
        result_directory,
        worker_entries=datasets.raw_entries if datasets is not None else (),
        endpoint_log_path=result_directory / "target-vm" / "endpoint.log",
        sampler_log_path=result_directory / "target-vm" / "sampler-console.log",
    )
    write_timeline_artifacts(result_directory, timeline_rows)
    _validate_migration(result_directory, len(requests), len(events), len(samples))
    for file_name in _LEGACY_FILES:
        path = result_directory / file_name
        if path.is_file():
            path.unlink()
    remove_intermediate_artifacts(result_directory)


def refresh_consolidated_run(result_directory: Path) -> None:
    environment_path = result_directory / "environment.json"
    environment = _read_json(environment_path)
    events = _read_json_lines(result_directory / "events.jsonl")
    samples = _read_json_lines(result_directory / "samples.jsonl")
    video_results = _read_json_lines(result_directory / "video-results.jsonl")
    timeline_rows, coverage = _rebuild_timeline(events, samples)
    runtime_config = environment.get("runtime_config", {})
    recovery_snapshot = (
        runtime_config.get("embedding_vm")
        if isinstance(runtime_config, dict)
        else None
    )
    errors = _legacy_observability_errors(
        request_count=len(video_results),
        timing_count=sum(row.get("pipeline_timing") is not None for row in video_results),
        stage_event_count=sum(
            str(row.get("event_type", "")).startswith("pipeline.stage.")
            for row in events
        ),
    )
    environment.update(
        {
            "status": "incomplete" if errors else "complete",
            "observability_status": "incomplete" if errors else "complete",
            "observability_errors": errors,
            "runtime_config": {
                "availability": "unavailable_legacy_not_captured"
            },
            "recovery_context": {
                "embedding_vm_snapshot_at_migration": recovery_snapshot,
                "warning": "This is not the historical test-time configuration.",
            }
            if recovery_snapshot is not None
            else environment.get("recovery_context", {}),
            "resource_coverage": coverage,
        }
    )
    environment_path.write_text(
        json.dumps(environment, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_timeline_artifacts(result_directory, timeline_rows)


def _read_requests(path: Path) -> tuple[CompleteRequestRecord, ...]:
    return tuple(
        CompleteRequestRecord(
            video_id=str(row["video_id"]),
            fixture=str(row["fixture"]),
            trace_id=str(row["trace_id"]),
            started_at=_parse_datetime(row["started_at"]),
            responded_at=_parse_datetime(row["responded_at"]),
            response_status=str(row["response_status"]),
            error=row.get("error"),
        )
        for row in _read_json_lines(path)
    )


def _read_terminal_statuses(path: Path) -> tuple[TerminalStatusRecord, ...]:
    return tuple(
        TerminalStatusRecord(
            video_id=str(row["video_id"]),
            status=str(row["terminal_status"]),
            observed_at=_parse_datetime(row["terminal_observed_at"]),
        )
        for row in _read_csv(path)
        if row.get("terminal_status") and row.get("terminal_observed_at")
    )


def _legacy_events(result_directory: Path) -> tuple[dict[str, Any], ...]:
    events = []
    for row in _read_json_lines(result_directory / "stage-events.jsonl"):
        stage_event = row.pop("event", "unknown")
        timestamp = row.pop("timestamp_utc", row.get("log_timestamp_utc"))
        events.append(
            {
                "timestamp_utc": timestamp,
                "source": "pipeline-worker",
                "event_type": f"pipeline.stage.{stage_event}",
                **row,
            }
        )
    for row in _read_json_lines(result_directory / "errors.jsonl"):
        events.append(
            {
                "timestamp_utc": None,
                "source": "load-test-driver",
                "event_type": "driver.error",
                "status": "failed",
                **row,
            }
        )
    return tuple(events)


def _legacy_samples(
    result_directory: Path,
    *,
    include_worker_samples: bool,
) -> tuple[dict[str, Any], ...]:
    worker_sources = (
        (result_directory / "queue-samples.csv", "pgmq", ","),
        (result_directory / "resource-samples" / "worker-process.csv", "worker-process", ","),
    )
    external_sources = (
        (
            result_directory / "resource-samples" / "cloud-monitoring.csv",
            "cloud-monitoring",
            ",",
        ),
        (result_directory / "target-vm" / "target-samples.tsv", "embedding-vm", "\t"),
    )
    sources = (*worker_sources, *external_sources) if include_worker_samples else external_sources
    samples = []
    for path, source, delimiter in sources:
        for row in _read_csv(path, delimiter=delimiter):
            row.pop("resource_sample_source", None)
            timestamp = row.pop("timestamp_utc", row.pop("log_timestamp_utc", None))
            samples.append(
                {"timestamp_utc": timestamp, "source": source, **_coerce_row(row)}
            )
    return tuple(samples)


def _legacy_observability_errors(
    *,
    request_count: int,
    timing_count: int,
    stage_event_count: int,
) -> list[str]:
    errors = []
    if timing_count != request_count:
        errors.append(
            f"Legacy pipeline timing coverage is {timing_count}/{request_count}."
        )
    expected_stage_events = request_count * 12
    if stage_event_count != expected_stage_events:
        errors.append(
            f"Legacy stage event coverage is {stage_event_count}/{expected_stage_events}."
        )
    return errors


def _rebuild_timeline(
    events: tuple[dict[str, Any], ...],
    samples: tuple[dict[str, Any], ...],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stage_events = tuple(
        {
            **row,
            "event": str(row["event_type"]).rsplit(".", 1)[-1],
        }
        for row in events
        if str(row.get("event_type", "")).startswith("pipeline.stage.")
    )
    queue_samples = tuple(row for row in samples if row.get("source") == "pgmq")
    resource_samples = tuple(row for row in samples if row.get("source") != "pgmq")
    return build_timeline(
        stage_events=stage_events,
        queue_samples=queue_samples,
        resource_samples=resource_samples,
    )


def _validate_migration(
    result_directory: Path,
    expected_results: int,
    expected_events: int,
    expected_samples: int,
) -> None:
    actual = {
        "video-results.jsonl": len(
            _read_json_lines(result_directory / "video-results.jsonl")
        ),
        "events.jsonl": len(_read_json_lines(result_directory / "events.jsonl")),
        "samples.jsonl": len(_read_json_lines(result_directory / "samples.jsonl")),
    }
    expected = {
        "video-results.jsonl": expected_results,
        "events.jsonl": expected_events,
        "samples.jsonl": expected_samples,
    }
    if actual != expected:
        raise ValueError(f"Migration row count mismatch: actual={actual}, expected={expected}")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _read_json_lines(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.is_file():
        return ()
    return tuple(
        payload
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        if isinstance((payload := json.loads(line)), dict)
    )


def _read_csv(path: Path, *, delimiter: str = ",") -> tuple[dict[str, str], ...]:
    if not path.is_file():
        return ()
    with path.open(encoding="utf-8", newline="") as csv_file:
        return tuple(csv.DictReader(csv_file, delimiter=delimiter))


def _coerce_row(row: dict[str, str]) -> dict[str, object]:
    return {key: _coerce(value) for key, value in row.items()}


def _coerce(value: str) -> str | int | float | bool:
    if value in {"true", "false"}:
        return value == "true"
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _parse_datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _timestamp_after(timestamp: str, seconds: float) -> str:
    return (
        _parse_datetime(timestamp) + timedelta(seconds=seconds)
    ).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directories", nargs="+", type=Path)
    parser.add_argument("--gcp-project-id")
    parser.add_argument("--recover-endpoint-logs", action="store_true")
    arguments = parser.parse_args()
    commands = CommandRunner() if arguments.gcp_project_id else None
    target_context = _target_context() if arguments.recover_endpoint_logs else None
    for result_directory in arguments.run_directories:
        if (result_directory / "video-results.jsonl").is_file() and not (
            result_directory / "requests.jsonl"
        ).is_file():
            refresh_consolidated_run(result_directory)
            print(f"Refreshed video pipeline artifacts: {result_directory}")
            continue
        embedding_runtime_config = (
            _recover_target_artifacts(result_directory.name, target_context)
            if target_context is not None
            else None
        )
        migrate_legacy_run(
            result_directory,
            commands=commands,
            gcp_project_id=arguments.gcp_project_id,
            embedding_runtime_config=embedding_runtime_config,
        )
        print(f"Migrated video pipeline artifacts: {result_directory}")
    return 0


def _target_context() -> tuple[TargetMonitor, ArtifactManager]:
    settings = Settings.from_environment()
    commands = CommandRunner()
    infrastructure = Infrastructure(settings, commands)
    infrastructure.prepare()
    monitor = TargetMonitor(
        settings,
        infrastructure,
        target_name=infrastructure.batch_target_name,
        target_zone=infrastructure.batch_target_zone,
    )
    return monitor, ArtifactManager(settings, infrastructure)


def _recover_target_artifacts(
    run_id: str,
    target_context: tuple[TargetMonitor, ArtifactManager],
) -> dict[str, Any]:
    monitor, artifacts = target_context
    monitor.collect_raw_endpoint_log(run_id)
    artifacts.collect_target_sampler_results(
        run_id,
        test_type="video-pipeline",
        target_name=monitor.target_name,
        target_zone=monitor.target_zone,
    )
    return monitor.deployment_snapshot()


if __name__ == "__main__":
    raise SystemExit(main())
