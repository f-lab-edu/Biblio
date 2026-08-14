from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path


LOAD_TEST_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOAD_TEST_DIR))

from video_pipeline.artifacts import write_video_pipeline_artifacts
from video_pipeline.fixtures import fixture_workload, load_fixture_manifest
from video_pipeline.models import (
    CompleteRequestRecord,
    DispatchPhase,
    TerminalStatusRecord,
)


class TestVideoPipelineFixturesAndArtifacts(unittest.TestCase):
    def test_manifest_validates_files_and_calculates_workload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fixtures = {}
            for kind, duration in (("short", 120), ("medium", 600), ("long", 1500)):
                fixture_path = root / f"{kind}.mp4"
                fixture_path.write_bytes(kind.encode("utf-8"))
                fixtures[kind] = {
                    "path": fixture_path.name,
                    "sha256": hashlib.sha256(kind.encode("utf-8")).hexdigest(),
                    "duration_seconds": duration,
                    "size_bytes": len(kind),
                }
            manifest_path = root / "fixtures.json"
            manifest_path.write_text(json.dumps({"fixtures": fixtures}), encoding="utf-8")

            loaded = load_fixture_manifest(manifest_path)
            workload = fixture_workload(
                (DispatchPhase("medium", 4, 4),),
                3,
                loaded,
            )

        self.assertEqual(workload["total_requests"], 12)
        self.assertEqual(workload["request_counts"]["medium"], 12)
        self.assertEqual(workload["total_fixture_duration_seconds"], 7200.0)

    def test_artifacts_preserve_request_and_terminal_timestamps(self) -> None:
        timestamp = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
        request = CompleteRequestRecord(
            video_id="video-1",
            fixture="short",
            trace_id="trace-1",
            started_at=timestamp,
            responded_at=timestamp,
            response_status="UPLOADED",
        )
        terminal = TerminalStatusRecord("video-1", "READY", timestamp)
        with tempfile.TemporaryDirectory() as temporary_directory:
            result_directory = Path(temporary_directory) / "video-pipeline"
            write_video_pipeline_artifacts(
                result_directory,
                run_metadata={"run_id": "run-1"},
                fixture_manifest={"short": {"path": "short.mp4"}},
                requests=(request,),
                terminal_statuses=(terminal,),
            )
            request_line = json.loads(
                (result_directory / "requests.jsonl").read_text(encoding="utf-8")
            )
            with (result_directory / "video-results.csv").open(
                encoding="utf-8", newline=""
            ) as csv_file:
                result_row = next(csv.DictReader(csv_file))

        self.assertEqual(request_line["trace_id"], "trace-1")
        self.assertEqual(request_line["started_at"], timestamp.isoformat())
        self.assertEqual(result_row["terminal_status"], "READY")


if __name__ == "__main__":
    unittest.main()
