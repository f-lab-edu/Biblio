from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


LOAD_TEST_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOAD_TEST_DIR))

from infrastructure import LoadTestError
from video_pipeline.models import (
    DispatchPhase,
    FixtureSpec,
    PreparedVideo,
    ScenarioPlan,
)
from video_pipeline.session import (
    complete_prepared_video,
    dispatch_complete_batch,
    execute_scenario,
    wait_for_terminal_status,
)


class _FakeVideoSessionClient:
    def __init__(self, statuses: list[str], *, failing_video_id: str = "") -> None:
        self._statuses = iter(statuses)
        self._failing_video_id = failing_video_id
        self._created_count = 0
        self.complete_calls: list[tuple[str, int, str | None]] = []
        self.get_calls: list[str] = []
        self.uploads: list[tuple[str, bytes]] = []

    def create_local_video(
        self,
        *,
        project_id: str,
        title: str,
        category: str,
        extension: str,
    ) -> dict[str, Any]:
        del project_id, title, category, extension
        self._created_count += 1
        return {
            "video_id": f"video-{self._created_count}",
            "signed_url": f"https://upload/{self._created_count}",
        }

    def upload_bytes(
        self,
        signed_url: str,
        payload: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        del content_type
        self.uploads.append((signed_url, payload))

    def complete_video(
        self,
        video_id: str,
        size_bytes: int,
        *,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        self.complete_calls.append((video_id, size_bytes, trace_id))
        if video_id == self._failing_video_id:
            raise RuntimeError("complete failed")
        return {"video_id": video_id, "status": "UPLOADED"}

    def get_video(self, video_id: str) -> dict[str, Any]:
        self.get_calls.append(video_id)
        return {"video_id": video_id, "status": next(self._statuses)}


class _AdvancingClock:
    def __init__(self) -> None:
        self.elapsed_seconds = 0.0
        self.started_at = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)

    def now(self) -> datetime:
        observed_at = self.started_at + timedelta(seconds=self.elapsed_seconds)
        self.elapsed_seconds += 1.0
        return observed_at

    def monotonic(self) -> float:
        return self.elapsed_seconds

    def sleep(self, seconds: float) -> None:
        self.elapsed_seconds += seconds


class TestVideoPipelineSession(unittest.TestCase):
    def test_complete_prepared_video_records_request_window(self) -> None:
        client = _FakeVideoSessionClient([])
        clock = _AdvancingClock()
        video = PreparedVideo("video-1", "medium", 123)

        record = complete_prepared_video(
            client,
            video,
            trace_id="trace-1",
            now=clock.now,
        )

        self.assertEqual(client.complete_calls, [("video-1", 123, "trace-1")])
        self.assertEqual(record.video_id, "video-1")
        self.assertEqual(record.response_status, "UPLOADED")
        self.assertLess(record.started_at, record.responded_at)

    def test_dispatch_complete_batch_uses_requested_concurrency(self) -> None:
        client = _FakeVideoSessionClient([])
        videos = [
            PreparedVideo(f"video-{index}", "medium", 123)
            for index in range(4)
        ]
        trace_ids = iter(f"trace-{index}" for index in range(4))

        records = dispatch_complete_batch(
            client,
            videos,
            concurrency=2,
            trace_id_factory=lambda: next(trace_ids),
        )

        self.assertEqual(
            [record.video_id for record in records],
            ["video-0", "video-1", "video-2", "video-3"],
        )
        self.assertCountEqual(
            client.complete_calls,
            [
                (f"video-{index}", 123, f"trace-{index}")
                for index in range(4)
            ],
        )

    def test_dispatch_complete_batch_rejects_invalid_concurrency(self) -> None:
        with self.assertRaisesRegex(LoadTestError, "concurrency"):
            dispatch_complete_batch(
                _FakeVideoSessionClient([]),
                [PreparedVideo("video-1", "medium", 123)],
                concurrency=0,
            )

    def test_dispatch_failure_preserves_each_attempt_record(self) -> None:
        client = _FakeVideoSessionClient([], failing_video_id="video-2")
        videos = [
            PreparedVideo("video-1", "medium", 123),
            PreparedVideo("video-2", "medium", 123),
        ]
        records = []

        with self.assertRaisesRegex(RuntimeError, "complete failed"):
            dispatch_complete_batch(
                client,
                videos,
                concurrency=1,
                record_sink=records.append,
            )

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].response_status, "UPLOADED")
        self.assertEqual(records[1].response_status, "ERROR")
        self.assertEqual(records[1].error, "complete failed")

    def test_execute_scenario_uploads_before_complete_and_observes_terminal(self) -> None:
        client = _FakeVideoSessionClient(["READY", "READY"])
        plan = ScenarioPlan(
            preset="TEST",
            repeat_count=1,
            phases=(DispatchPhase("short", 2, 2),),
            is_baseline=False,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture_path = Path(temporary_directory) / "short.mp4"
            fixture_path.write_bytes(b"fixture")
            fixture = FixtureSpec(
                kind="short",
                path=fixture_path,
                sha256="unused",
                duration_seconds=120.0,
                size_bytes=7,
            )

            requests, terminals = execute_scenario(
                client,
                plan,
                {"short": fixture},
                project_id="project-1",
                run_label="run-1",
                terminal_timeout_seconds=10.0,
                poll_interval_seconds=0.01,
            )

        self.assertEqual(len(client.uploads), 2)
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(terminals), 2)
        self.assertEqual({record.status for record in terminals}, {"READY"})

    def test_wait_for_terminal_status_records_ready_observation(self) -> None:
        client = _FakeVideoSessionClient(["PROCESSING", "READY"])
        clock = _AdvancingClock()

        record = wait_for_terminal_status(
            client,
            "video-1",
            timeout_seconds=30.0,
            poll_interval_seconds=5.0,
            now=clock.now,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

        self.assertEqual(record.status, "READY")
        self.assertEqual(client.get_calls, ["video-1", "video-1"])

    def test_wait_for_terminal_status_reports_last_status_on_timeout(self) -> None:
        client = _FakeVideoSessionClient(["PROCESSING", "PROCESSING"])
        clock = _AdvancingClock()

        with self.assertRaisesRegex(LoadTestError, "last status was PROCESSING"):
            wait_for_terminal_status(
                client,
                "video-1",
                timeout_seconds=2.0,
                poll_interval_seconds=1.0,
                now=clock.now,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )


if __name__ == "__main__":
    unittest.main()
