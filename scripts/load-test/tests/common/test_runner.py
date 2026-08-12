from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

LOAD_TEST_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOAD_TEST_DIR))

from infrastructure import CommandRunner, Infrastructure, JsonState, LoadTestError
from k6_runner import ArtifactManager, K6Runner, ScenarioRequest
from tests.helpers import DownloadInfrastructure, FakeInfrastructure, settings_for, write_json


class TestJsonState(unittest.TestCase):
    def test_round_trip_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state = JsonState(Path(temporary_directory) / "state.json")
            state.write({"status": "active", "rate": 3})
            self.assertEqual(state.read(), {"status": "active", "rate": 3})
            state.delete()
            self.assertFalse(state.exists())

    def test_rejects_non_object_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "state.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(LoadTestError, "must contain a JSON object"):
                JsonState(path).read()


class TestRunnerCleanup(unittest.TestCase):
    def test_stops_runner_when_sync_state_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = settings_for(root)
            settings.load_test_root.mkdir(parents=True)
            (settings.load_test_root / "smoke.js").write_text(
                "export default function() {}", encoding="utf-8"
            )
            fake = FakeInfrastructure()
            runner = K6Runner(
                settings,
                CommandRunner(),
                cast(Infrastructure, fake),
                cast(ArtifactManager, object()),
            )
            with self.assertRaisesRegex(LoadTestError, "State file does not exist"):
                runner.run_scenario(
                    ScenarioRequest(
                        scenario="smoke.js", target_url="https://example.test"
                    )
                )
            self.assertTrue(fake.stop_called)

    def test_stops_runner_when_target_url_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = settings_for(Path(temporary_directory))
            fake = FakeInfrastructure()
            runner = K6Runner(
                settings,
                CommandRunner(),
                cast(Infrastructure, fake),
                cast(ArtifactManager, object()),
            )
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(LoadTestError, "TARGET_URL is required"):
                    runner.run_from_environment("smoke.js")
            self.assertTrue(fake.stop_called)


class TestArtifactCollection(unittest.TestCase):
    def test_metric_value_supports_nested_summary_values(self) -> None:
        metrics = {"dropped_iterations": {"values": {"count": 3}}}

        result = ArtifactManager._metric_value(
            metrics, "dropped_iterations", "count"
        )

        self.assertAlmostEqual(result, 3.0)

    def test_partial_result_retry_does_not_create_nested_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = settings_for(root)
            manager = ArtifactManager(
                settings, cast(Infrastructure, DownloadInfrastructure())
            )
            manager.run_state.write(
                {
                    "run_id": "test-run",
                    "scenario": "search-embedding",
                    "remote_result": "~/results/test-run/search-embedding",
                }
            )
            partial = settings.artifact_root / "test-run/search-embedding"
            write_json(partial / "summary.json", {})

            result = manager.collect_runner_results()

            self.assertTrue((result / "runner-metrics.json").is_file())
            self.assertFalse((result / "search-embedding").exists())

    def test_zero_byte_runner_result_is_downloaded_again(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = settings_for(root)
            fake = DownloadInfrastructure()
            manager = ArtifactManager(settings, cast(Infrastructure, fake))
            manager.run_state.write(
                {
                    "run_id": "test-run",
                    "scenario": "search-embedding",
                    "remote_result": "~/results/test-run/search-embedding",
                }
            )
            partial = settings.artifact_root / "test-run/search-embedding"
            for name in (
                "summary.json",
                "raw.json.gz",
                "console.log",
                "runner-metrics.json",
            ):
                path = partial / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"")

            manager.collect_runner_results()

            self.assertEqual(fake.scp_calls, 1)
            self.assertGreater((partial / "summary.json").stat().st_size, 0)

    def test_partial_target_result_is_downloaded_without_nesting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = settings_for(root)
            fake = DownloadInfrastructure(target_download=True)
            manager = ArtifactManager(settings, cast(Infrastructure, fake))
            target = settings.artifact_root / "test-run/search-embedding/target-vm"
            write_json(target / "target-metrics.json", {})

            result = manager.collect_target_results("test-run")

            self.assertEqual(fake.scp_calls, 1)
            self.assertTrue((result / "admission-summary.json").is_file())
            self.assertFalse((result / "test-run").exists())


class TestCommonRegressions(unittest.TestCase):
    def test_missing_raw_output_does_not_abort_remote_cleanup(self) -> None:
        executor = (LOAD_TEST_DIR / "remote/k6-executor.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('if [[ -f "$raw_path" ]]; then', executor)
        self.assertNotIn('[[ -f "$raw_path" ]] && gzip', executor)

    def test_settings_has_no_stale_project_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            self.assertFalse(
                hasattr(settings_for(Path(temporary_directory)), "project_id")
            )
