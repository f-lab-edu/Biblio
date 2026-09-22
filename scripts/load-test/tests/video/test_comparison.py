from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


LOAD_TEST_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOAD_TEST_DIR))

from video_pipeline.comparison import build_schema_v2_pipeline_timings
from video_pipeline.models import CompleteRequestRecord
from video_pipeline.observability import WorkerLogDatasets


def _work_event(
    timestamp: str,
    event_type: str,
    stage: str,
    work_id: str,
    *,
    video_id: str = "video-1",
) -> dict[str, object]:
    return {
        "timestamp_utc": timestamp,
        "event_type": event_type,
        "stage": stage,
        "work_id": work_id,
        "work_attempt": 1,
        "read_ct": 1,
        "video_id": video_id,
    }


class TestSchemaV2ComparisonTiming(unittest.TestCase):
    def test_builds_legacy_compatible_timing_from_work_lifecycle(self) -> None:
        events = (
            _work_event(
                "2026-08-14T12:00:00Z",
                "pipeline.work.started",
                "NORMALIZE_VIDEO",
                "run-1",
            ),
            _work_event(
                "2026-08-14T12:00:01Z",
                "pipeline.work.succeeded",
                "NORMALIZE_VIDEO",
                "run-1",
            ),
            _work_event(
                "2026-08-14T12:00:01Z",
                "pipeline.work.started",
                "TRANSCRIBE_PART",
                "part-1",
            ),
            _work_event(
                "2026-08-14T12:00:03Z",
                "pipeline.work.succeeded",
                "TRANSCRIBE_PART",
                "part-1",
            ),
            {
                **_work_event(
                    "2026-08-14T12:00:03Z",
                    "assembly.started",
                    "ASSEMBLE_CHUNKS",
                    "assembly-1",
                ),
                "pipeline_run_id": "run-1",
            },
            {
                **_work_event(
                    "2026-08-14T12:00:03.500000Z",
                    "assembly.succeeded",
                    "ASSEMBLE_CHUNKS",
                    "assembly-1",
                ),
                "pipeline_run_id": "run-1",
            },
            _work_event(
                "2026-08-14T12:00:04Z",
                "pipeline.work.started",
                "ENRICH_CHUNK",
                "chunk-1",
            ),
            _work_event(
                "2026-08-14T12:00:06Z",
                "pipeline.work.succeeded",
                "ENRICH_CHUNK",
                "chunk-1",
            ),
            _work_event(
                "2026-08-14T12:00:06Z",
                "pipeline.work.started",
                "EMBED_BATCH",
                "batch-1",
                video_id="-",
            ),
            _work_event(
                "2026-08-14T12:00:08Z",
                "pipeline.work.succeeded",
                "EMBED_BATCH",
                "batch-1",
                video_id="-",
            ),
            {
                "timestamp_utc": "2026-08-14T12:00:08Z",
                "event_type": "embedding.batch.completed",
                "batch_id": "batch-1",
                "participant_video_ids": ["video-1"],
            },
            {
                "timestamp_utc": "2026-08-14T12:00:08.100000Z",
                "event_type": "pipeline.video.completed",
                "video_id": "video-1",
                "batch_id": "batch-1",
            },
        )
        request = CompleteRequestRecord(
            video_id="video-1",
            fixture="medium",
            trace_id="trace-1",
            started_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
            responded_at=datetime(2026, 8, 14, 12, 0, 0, 100000, tzinfo=UTC),
            response_status="UPLOADED",
        )

        timings = build_schema_v2_pipeline_timings(
            WorkerLogDatasets(events, (), (), ()),
            (request,),
        )

        timing = timings[0]
        self.assertEqual(timing["trace_id"], "trace-1")
        self.assertAlmostEqual(timing["audio_ms"], 1000.0)
        self.assertAlmostEqual(timing["stt_ms"], 2000.0)
        self.assertAlmostEqual(timing["chunk_enrichment_ms"], 3000.0)
        self.assertAlmostEqual(timing["embedding_ms"], 2000.0)
        self.assertAlmostEqual(timing["persist_ms"], 100.0)
        self.assertAlmostEqual(timing["total_ms"], 8100.0)
        self.assertEqual(timing["timing_source"], "schema-v2-lifecycle")


if __name__ == "__main__":
    unittest.main()
