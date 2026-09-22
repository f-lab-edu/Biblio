from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


LOAD_TEST_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOAD_TEST_DIR))

from video_pipeline.monitoring import collect_cloud_run_monitoring_samples


class _Commands:
    def output(self, command: list[str]) -> str:
        self.command = command
        return "access-token"


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class TestVideoPipelineMonitoring(unittest.TestCase):
    def test_collects_cloud_run_distribution_and_instance_samples(self) -> None:
        timestamp = "2026-08-14T12:01:00Z"
        responses = [
            _Response(
                {
                    "timeSeries": [
                        {
                            "points": [
                                {
                                    "interval": {"endTime": timestamp},
                                    "value": {"distributionValue": {"mean": 0.25}},
                                }
                            ]
                        }
                    ]
                }
            ),
            _Response(
                {
                    "timeSeries": [
                        {
                            "points": [
                                {
                                    "interval": {"endTime": timestamp},
                                    "value": {"distributionValue": {"mean": 0.5}},
                                }
                            ]
                        }
                    ]
                }
            ),
            _Response(
                {
                    "timeSeries": [
                        {
                            "points": [
                                {
                                    "interval": {"endTime": timestamp},
                                    "value": {"int64Value": "1"},
                                }
                            ]
                        }
                    ]
                }
            ),
        ]
        commands = _Commands()

        with patch(
            "video_pipeline.monitoring.urllib.request.urlopen",
            side_effect=responses,
        ):
            samples = collect_cloud_run_monitoring_samples(
                commands,
                project_id="gcp-project",
                service_name="pipeline-worker",
                start_time="2026-08-14T12:00:00Z",
                end_time="2026-08-14T12:02:00Z",
            )

        self.assertEqual(commands.command, ["gcloud", "auth", "print-access-token"])
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["worker_cpu_percent"], 25.0)
        self.assertEqual(samples[0]["worker_memory_percent"], 50.0)
        self.assertEqual(samples[0]["worker_instance_count"], 1.0)

    def test_aggregates_multiple_series_at_the_same_timestamp(self) -> None:
        timestamp = "2026-08-14T12:01:00Z"
        responses = [
            _Response(
                {
                    "timeSeries": [
                        {
                            "points": [
                                {
                                    "interval": {"endTime": timestamp},
                                    "value": {"distributionValue": {"mean": mean}},
                                }
                            ]
                        }
                        for mean in (0.2, 0.4)
                    ]
                }
            ),
            _Response({"timeSeries": []}),
            _Response(
                {
                    "timeSeries": [
                        {
                            "points": [
                                {
                                    "interval": {"endTime": timestamp},
                                    "value": {"int64Value": count},
                                }
                            ]
                        }
                        for count in ("1", "2")
                    ]
                }
            ),
        ]

        with patch(
            "video_pipeline.monitoring.urllib.request.urlopen",
            side_effect=responses,
        ):
            samples = collect_cloud_run_monitoring_samples(
                _Commands(),
                project_id="gcp-project",
                service_name="pipeline-worker",
                start_time="2026-08-14T12:00:00Z",
                end_time="2026-08-14T12:02:00Z",
            )

        self.assertAlmostEqual(samples[0]["worker_cpu_percent"], 30.0)
        self.assertEqual(samples[0]["worker_instance_count"], 3.0)


if __name__ == "__main__":
    unittest.main()
