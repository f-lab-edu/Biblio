from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


LOAD_TEST_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOAD_TEST_DIR))

from video_pipeline.observability import (
    WorkerLogDatasets,
    event_trace_ids,
    parse_embedding_endpoint_log,
    parse_worker_logs,
    select_run_traces,
)
from infrastructure import LoadTestError
from video_pipeline.timeline import build_timeline, write_timeline_artifacts


def _entry(
    timestamp: str,
    message: str,
    *,
    video_id: str = "video-1",
) -> dict[str, str]:
    return {
        "timestamp": timestamp,
        "textPayload": (
            f"2026-08-14 12:00:00.000 | INFO | trace_id=trace-1 "
            f"video_id={video_id} user_id=- | {message}"
        ),
    }


class TestVideoPipelineObservability(unittest.TestCase):
    def test_schema_v2_run_selection_follows_run_and_batch_identifiers(self) -> None:
        events = (
            {
                "event_type": "normalization.completed",
                "trace_id": "request-trace",
                "video_id": "video-1",
                "pipeline_run_id": "run-1",
            },
            {
                "event_type": "enrichment.completed",
                "trace_id": "scheduler-trace",
                "video_id": "video-1",
                "pipeline_run_id": "run-1",
                "work_id": "chunk-1",
            },
            {
                "event_type": "embedding.batch.completed",
                "trace_id": "scheduler-trace",
                "video_id": "-",
                "pipeline_run_id": "-",
                "work_id": "batch-1",
                "batch_id": "batch-1",
                "participant_run_ids": ["run-1"],
            },
            {
                "event_type": "pipeline.work.started",
                "trace_id": "scheduler-trace",
                "video_id": "-",
                "pipeline_run_id": "-",
                "work_id": "batch-1",
                "stage": "EMBED_BATCH",
            },
            {
                "event_type": "embedding.request.success",
                "trace_id": "scheduler-trace",
                "video_id": "-",
            },
            {
                "event_type": "enrichment.completed",
                "trace_id": "scheduler-trace",
                "video_id": "foreign-video",
                "pipeline_run_id": "foreign-run",
                "work_id": "foreign-work",
            },
        )
        datasets = select_run_traces(
            WorkerLogDatasets(events, (), (), ()),
            {"request-trace"},
            video_ids={"video-1"},
        )

        self.assertEqual(len(datasets.events), 5)
        selected_video_ids = {row.get("video_id") for row in datasets.events}
        self.assertNotIn("foreign-video", selected_video_ids)
        batch_event = next(
            row
            for row in datasets.events
            if row.get("event_type") == "embedding.batch.completed"
        )
        self.assertEqual(batch_event["participant_video_ids"], ["video-1"])
        self.assertEqual(
            event_trace_ids(datasets),
            {"request-trace", "scheduler-trace"},
        )

    def test_schema_v2_json_payload_is_parsed_without_dropping_v1(self) -> None:
        datasets = parse_worker_logs(
            [
                _entry(
                    "2026-08-14T12:00:00Z",
                    "pipeline.stage timestamp_utc=2026-08-14T12:00:00+00:00 "
                    "stage=stt event=started status=running",
                ),
                {
                    "timestamp": "2026-08-14T12:00:01Z",
                    "jsonPayload": {
                        "log_schema_version": 2,
                        "timestamp_utc": "2026-08-14T12:00:01+00:00",
                        "event_name": "pipeline.work.started",
                        "message": "pipeline.work.started",
                        "trace_id": "trace-2",
                        "video_id": "video-2",
                        "pipeline_run_id": "run-2",
                        "stage": "TRANSCRIBE_PART",
                        "work_id": "part-2",
                        "work_attempt": 1,
                        "read_ct": 1,
                    },
                },
                {
                    "timestamp": "2026-08-14T12:00:02Z",
                    "jsonPayload": {
                        "log_schema_version": 2,
                        "timestamp_utc": "2026-08-14T12:00:02+00:00",
                        "event_name": "stage.work.sample",
                        "message": "stage.work.sample",
                        "stage": "TRANSCRIBE_PART",
                        "ready_count": 2,
                        "running_count": 1,
                    },
                },
            ]
        )

        self.assertEqual(
            [event["event_type"] for event in datasets.events],
            ["pipeline.stage.started", "pipeline.work.started"],
        )
        self.assertEqual(datasets.samples[0]["source"], "pipeline-db")
        self.assertEqual(datasets.samples[0]["ready_count"], 2)

    def test_schema_v2_event_embedded_in_text_payload_is_recovered(self) -> None:
        datasets = parse_worker_logs(
            [
                {
                    "timestamp": "2026-08-14T12:00:01Z",
                    "textPayload": (
                        '  Duration: {"log_schema_version": 2, '
                        '"timestamp_utc": "2026-08-14T12:00:01+00:00", '
                        '"message": "assembly.started", '
                        '"event_name": "assembly.started", '
                        '"stage": "ASSEMBLE_CHUNKS", '
                        '"pipeline_run_id": "run-1", '
                        '"trace_id": "trace-1", '
                        '"work_id": "assembly-1", "video_id": "-"}'
                    ),
                }
            ]
        )

        self.assertEqual(len(datasets.events), 1)
        self.assertEqual(datasets.events[0]["event_type"], "assembly.started")
        self.assertEqual(datasets.events[0]["work_id"], "assembly-1")

    def test_worker_logs_are_grouped_as_events_timings_and_samples(self) -> None:
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
                _entry(
                    "2026-08-14T12:00:04Z",
                    "event=embedding.request.retry model_version=bge-m3 "
                    "attempt=1 duration_ms=20000.0 status_code=503",
                ),
            ]
        )

        self.assertEqual(len(datasets.events), 2)
        self.assertEqual(datasets.events[0]["event_type"], "pipeline.stage.started")
        self.assertEqual(datasets.events[1]["event_type"], "embedding.request.retry")
        self.assertEqual(datasets.queue_samples[0]["ready"], 3)
        self.assertEqual(datasets.worker_process_samples[0]["rss_bytes"], 1048576)
        self.assertEqual(datasets.pipeline_timings[0]["total_ms"], 5000.0)

    def test_endpoint_events_are_filtered_to_run_traces(self) -> None:
        rows = (
            {
                "ts": "2026-08-14T12:00:00Z",
                "msg": "embedding.admission",
                "trace_id": "trace-1",
                "admission_result": "granted",
            },
            {
                "ts": "2026-08-14T12:00:01Z",
                "msg": "embedding.admission",
                "trace_id": "foreign-trace",
                "admission_result": "granted",
            },
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            endpoint_log = Path(temporary_directory) / "endpoint.log"
            endpoint_log.write_text(
                "\n".join(json.dumps(row) for row in rows),
                encoding="utf-8",
            )
            events = parse_embedding_endpoint_log(
                endpoint_log,
                trace_ids={"trace-1"},
            )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "embedding.admission")
        self.assertEqual(events[0]["source"], "embedding-vm")

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
            write_timeline_artifacts(result_directory, rows)
            self.assertTrue((result_directory / "timeline.csv").is_file())
            self.assertFalse((result_directory / "resource-coverage.json").exists())

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

    def test_timeline_allows_overlapping_work_for_same_video(self) -> None:
        stage_events = (
            {
                "timestamp_utc": "2026-08-14T12:00:00+00:00",
                "video_id": "video-1",
                "stage": "TRANSCRIBE_PART",
                "work_id": "part-1",
                "work_attempt": 1,
                "read_ct": 1,
                "event": "started",
            },
            {
                "timestamp_utc": "2026-08-14T12:00:01+00:00",
                "video_id": "video-1",
                "stage": "TRANSCRIBE_PART",
                "work_id": "part-2",
                "work_attempt": 1,
                "read_ct": 1,
                "event": "started",
            },
            {
                "timestamp_utc": "2026-08-14T12:00:04+00:00",
                "video_id": "video-1",
                "stage": "TRANSCRIBE_PART",
                "work_id": "part-1",
                "work_attempt": 1,
                "read_ct": 1,
                "event": "finished",
            },
            {
                "timestamp_utc": "2026-08-14T12:00:05+00:00",
                "video_id": "video-1",
                "stage": "TRANSCRIBE_PART",
                "work_id": "part-2",
                "work_attempt": 1,
                "read_ct": 1,
                "event": "finished",
            },
        )
        resource_samples = (
            {
                "timestamp_utc": "2026-08-14T12:00:02+00:00",
                "resource_sample_source": "worker-process",
                "cpu_percent": 30.0,
            },
        )

        rows, _ = build_timeline(
            stage_events=stage_events,
            queue_samples=(),
            resource_samples=resource_samples,
        )

        self.assertEqual(rows[0]["active_video_count"], 1)
        self.assertEqual(rows[0]["active_work_count"], 2)
        self.assertEqual(rows[0]["transcription_active_count"], 2)


if __name__ == "__main__":
    unittest.main()
