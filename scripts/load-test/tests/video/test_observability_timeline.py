from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


LOAD_TEST_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOAD_TEST_DIR))

from video_pipeline.observability import parse_worker_logs, write_worker_log_datasets
from infrastructure import LoadTestError
from video_pipeline.timeline import build_timeline, write_timeline_artifacts


def _entry(timestamp: str, message: str, *, video_id: str = "video-1") -> dict[str, str]:
    return {
        "timestamp": timestamp,
        "textPayload": (
            f"2026-08-14 12:00:00.000 | INFO | trace_id=trace-1 "
            f"video_id={video_id} user_id=- | {message}"
        ),
    }


class TestVideoPipelineObservability(unittest.TestCase):
    def test_worker_logs_are_split_into_required_datasets(self) -> None:
        datasets = parse_worker_logs(
            [
                _entry(
                    "2026-08-14T12:00:00Z",
                    "pipeline.stage timestamp_utc=2026-08-14T12:00:00+00:00 "
                    "stage=stt event=started status=running",
                ),
                _entry(
                    "2026-08-14T12:00:01Z",
                    "queue.sample timestamp_utc=2026-08-14T12:00:01+00:00 "
                    "queue=PREPROCESS_REQUEST ready=3 invisible=1 oldest_age_sec=5.0",
                    video_id="-",
                ),
                _entry(
                    "2026-08-14T12:00:02Z",
                    "worker.process.sample timestamp_utc=2026-08-14T12:00:02+00:00 "
                    "cpu_percent=25.0 rss_bytes=1048576",
                    video_id="-",
                ),
                _entry(
                    "2026-08-14T12:00:03Z",
                    "pipeline.timing status=success stt_ms=3000.0 total_ms=5000.0",
                ),
            ]
        )

        self.assertEqual(len(datasets.stage_events), 1)
        self.assertEqual(datasets.queue_samples[0]["ready"], 3)
        self.assertEqual(datasets.worker_process_samples[0]["rss_bytes"], 1048576)
        self.assertEqual(datasets.pipeline_timings[0]["total_ms"], 5000.0)

        with tempfile.TemporaryDirectory() as temporary_directory:
            result_directory = Path(temporary_directory)
            write_worker_log_datasets(result_directory, datasets)
            self.assertTrue((result_directory / "stage-events.jsonl").is_file())
            self.assertTrue((result_directory / "queue-samples.csv").is_file())
            self.assertTrue(
                (result_directory / "resource-samples/worker-process.csv").is_file()
            )

    def test_timeline_uses_actual_resource_times_and_marks_fallback(self) -> None:
        stage_events = (
            {
                "timestamp_utc": "2026-08-14T12:00:00+00:00",
                "video_id": "video-1",
                "stage": "stt",
                "event": "started",
            },
            {
                "timestamp_utc": "2026-08-14T12:00:10+00:00",
                "video_id": "video-1",
                "stage": "stt",
                "event": "finished",
            },
        )
        queue_samples = (
            {
                "timestamp_utc": "2026-08-14T12:00:01+00:00",
                "ready": 2,
                "invisible": 1,
                "oldest_age_sec": 3.0,
            },
        )
        resource_samples = (
            {
                "timestamp_utc": "2026-08-14T12:00:02+00:00",
                "resource_sample_source": "cloud-monitoring",
                "worker_cpu_percent": 20.0,
            },
            {
                "timestamp_utc": "2026-08-14T12:00:03+00:00",
                "resource_sample_source": "worker-process",
                "cpu_percent": 25.0,
            },
        )

        rows, coverage = build_timeline(
            stage_events=stage_events,
            queue_samples=queue_samples,
            resource_samples=resource_samples,
        )

        self.assertEqual(rows[0]["timestamp_utc"], "2026-08-14T12:00:02+00:00")
        self.assertEqual(rows[0]["active_video_count"], 1)
        self.assertEqual(rows[0]["stt_active_count"], 1)
        self.assertEqual(rows[0]["queue_ready_count"], 2)
        self.assertTrue(coverage["stages"][0]["worker_process_fallback_required"])

        with tempfile.TemporaryDirectory() as temporary_directory:
            result_directory = Path(temporary_directory)
            write_timeline_artifacts(result_directory, rows, coverage)
            self.assertTrue((result_directory / "timeline.csv").is_file())
            self.assertTrue((result_directory / "resource-coverage.json").is_file())

    def test_timeline_rejects_finished_event_without_started_event(self) -> None:
        stage_events = (
            {
                "timestamp_utc": "2026-08-14T12:00:10+00:00",
                "video_id": "video-1",
                "stage": "stt",
                "event": "finished",
            },
        )

        with self.assertRaisesRegex(LoadTestError, "finished without started"):
            build_timeline(
                stage_events=stage_events,
                queue_samples=(),
                resource_samples=(),
            )


if __name__ == "__main__":
    unittest.main()
