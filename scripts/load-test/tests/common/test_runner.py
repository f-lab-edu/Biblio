from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

LOAD_TEST_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOAD_TEST_DIR))

from infrastructure import (
    CommandRunner,
    Infrastructure,
    JsonState,
    LoadTestError,
    artifact_type_for_scenario,
)
from k6_runner import ArtifactManager, K6Runner, ScenarioRequest
from runner import (
    _GCloudIdentityTokenProvider,
    _finish_video_embedding_sampler,
    _start_target_monitor,
    build_parser,
)
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


class TestArtifactPaths(unittest.TestCase):
    def test_settings_builds_test_type_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = settings_for(Path(temporary_directory))

            result = settings.artifact_run_directory("video-pipeline", "test-run")

            self.assertEqual(
                result,
                settings.artifact_root / "video-pipeline/test-run",
            )

    def test_scenarios_map_to_stable_artifact_types(self) -> None:
        expected_types = {
            "smoke": "smoke",
            "search-embedding": "search-embedding",
            "batch-embedding-capacity": "batch-embedding",
            "video-pipeline": "video-pipeline",
        }

        for scenario, expected_type in expected_types.items():
            with self.subTest(scenario=scenario):
                self.assertEqual(
                    artifact_type_for_scenario(scenario),
                    expected_type,
                )


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
                    "test_type": "search-embedding",
                    "remote_result": "~/results/test-run/search-embedding",
                }
            )
            partial = settings.artifact_root / "search-embedding/test-run"
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
            partial = settings.artifact_root / "search-embedding/test-run"
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
            target = settings.artifact_root / "search-embedding/test-run/target-vm"
            write_json(target / "target-metrics.json", {})

            result = manager.collect_target_results("test-run")

            self.assertEqual(fake.scp_calls, 1)
            self.assertTrue((result / "admission-summary.json").is_file())
            self.assertFalse((result / "test-run").exists())

    def test_collects_sampler_only_target_result_for_video_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = settings_for(root)
            fake = DownloadInfrastructure(target_download=True)
            manager = ArtifactManager(settings, cast(Infrastructure, fake))
            settings.artifact_run_directory(
                "video-pipeline", "test-run"
            ).mkdir(parents=True)

            result = manager.collect_target_sampler_results(
                "test-run",
                test_type="video-pipeline",
                target_name="batch-target",
                target_zone="batch-zone",
            )

            self.assertEqual(
                fake.last_source,
                "batch-target:~/biblio-target-load-results/test-run",
            )
            self.assertEqual(fake.last_zone, "batch-zone")
            self.assertTrue((result / "target-metrics.json").is_file())
            self.assertTrue((result / "target-samples.tsv").is_file())


class TestCommonRegressions(unittest.TestCase):
    def test_user_identity_token_does_not_request_a_custom_audience(self) -> None:
        commands = Mock()
        commands.output.return_value = "identity-token"

        token = _GCloudIdentityTokenProvider(commands).identity_token(
            "https://core.example.test"
        )

        self.assertEqual(token, "identity-token")
        commands.output.assert_called_once_with(
            ["gcloud", "auth", "print-identity-token"]
        )

    def test_video_pipeline_plan_accepts_workload_overrides(self) -> None:
        arguments = build_parser().parse_args(
            [
                "video-pipeline-plan",
                "--preset",
                "S3",
                "--fixtures-manifest",
                "fixtures.json",
                "--request-count",
                "12",
                "--concurrency",
                "6",
            ]
        )

        self.assertEqual(arguments.command, "video-pipeline-plan")
        self.assertEqual(arguments.request_count, 12)
        self.assertEqual(arguments.concurrency, 6)

    def test_video_pipeline_run_collects_embedding_samples_by_default(self) -> None:
        arguments = build_parser().parse_args(
            [
                "video-pipeline-run",
                "--preset",
                "S1",
                "--fixtures-manifest",
                "fixtures.json",
                "--biblio-project-id",
                "project-id",
            ]
        )

        self.assertIsNone(arguments.embedding_vm_samples)

    def test_sampler_result_is_collected_even_when_stop_fails(self) -> None:
        monitor = Mock(target_name="batch-target", target_zone="batch-zone")
        monitor.stop.side_effect = LoadTestError("stop timeout")
        artifacts = Mock()

        errors = _finish_video_embedding_sampler(
            monitor,
            artifacts,
            "test-run",
        )

        self.assertEqual(errors, ["Embedding VM sampler stop failed: stop timeout"])
        artifacts.collect_target_sampler_results.assert_called_once_with(
            "test-run",
            test_type="video-pipeline",
            target_name="batch-target",
            target_zone="batch-zone",
        )

    def test_sampler_start_failure_attempts_cleanup(self) -> None:
        monitor = Mock()
        monitor.start.side_effect = LoadTestError("start timeout")

        with self.assertRaisesRegex(LoadTestError, "start timeout"):
            _start_target_monitor(monitor, "test-run")

        monitor.stop.assert_called_once_with("test-run")

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
