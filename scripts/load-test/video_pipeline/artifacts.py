from __future__ import annotations

import csv
import json
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
    errors: tuple[str, ...] = (),
) -> None:
    result_directory.mkdir(parents=True, exist_ok=True)
    _write_json(result_directory / "environment.json", run_metadata)
    _write_json(result_directory / "fixtures.json", fixture_manifest)
    _write_json_lines(result_directory / "requests.jsonl", requests)
    _write_video_results(
        result_directory / "video-results.csv",
        requests,
        terminal_statuses,
    )
    _write_json_lines(
        result_directory / "errors.jsonl",
        tuple({"error": error} for error in errors),
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


def _write_video_results(
    path: Path,
    requests: tuple[CompleteRequestRecord, ...],
    terminal_statuses: tuple[TerminalStatusRecord, ...],
) -> None:
    terminal_by_video = {record.video_id: record for record in terminal_statuses}
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=(
                "video_id",
                "fixture",
                "trace_id",
                "complete_started_at",
                "complete_responded_at",
                "response_status",
                "request_error",
                "terminal_status",
                "terminal_observed_at",
            ),
        )
        writer.writeheader()
        for request in requests:
            terminal = terminal_by_video.get(request.video_id)
            writer.writerow(
                {
                    "video_id": request.video_id,
                    "fixture": request.fixture,
                    "trace_id": request.trace_id,
                    "complete_started_at": request.started_at.isoformat(),
                    "complete_responded_at": request.responded_at.isoformat(),
                    "response_status": request.response_status,
                    "request_error": request.error or "",
                    "terminal_status": terminal.status if terminal else "",
                    "terminal_observed_at": (
                        terminal.observed_at.isoformat() if terminal else ""
                    ),
                }
            )


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__} to JSON.")
