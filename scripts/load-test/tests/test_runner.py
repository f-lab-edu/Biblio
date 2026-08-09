from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from uuid import UUID
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from infrastructure import (
    CommandRunner,
    Infrastructure,
    JsonState,
    LoadTestError,
    Settings,
)
from k6_runner import ArtifactManager, K6Runner, ScenarioRequest
from runner import build_parser, search_run_config
from search_embedding import SearchEmbeddingSession, SearchRunConfig, duration_seconds
from search_target import SearchTarget


def settings_for(root: Path) -> Settings:
    return Settings(
        script_dir=root,
        repo_root=root,
        terraform_dir=root,
        load_test_root=root / "load-tests/k6",
        artifact_root=root / "artifacts",
        runner_network_capacity_bps=500_000_000,
        target_network_capacity_bps=0,
    )


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class FakeInfrastructure:
    runner_name = "runner"
    runner_zone = "test-zone"

    def __init__(self) -> None:
        self.stop_called = False

    def runner_status(self) -> str:
        return "RUNNING"

    def stop_runner(self) -> None:
        self.stop_called = True


class DownloadInfrastructure(FakeInfrastructure):
    search_target_name = "target"
    search_target_zone = "target-zone"

    def __init__(self, target_download: bool = False) -> None:
        super().__init__()
        self.target_download = target_download
        self.scp_calls = 0

    def scp(
        self,
        _source: str,
        destination: str,
        *,
        zone: str,
        recursive: bool = False,
    ) -> None:
        self.scp_calls += 1
        self.last_zone = zone
        self.last_recursive = recursive
        if self.target_download:
            self._write_target_result(Path(destination) / "test-run")
            return
        result = Path(destination) / "search-embedding"
        write_json(
            result / "summary.json",
            {"metrics": {"dropped_iterations": {"values": {"count": 0}}}},
        )
        write_json(
            result / "runner-metrics.json",
            {
                "max_cpu_percent": 20,
                "max_memory_percent": 30,
                "network_saturation_detected": False,
                "file_descriptor_error_detected": False,
                "vm_restart_detected": False,
            },
        )
        (result / "raw.json.gz").write_bytes(b"raw")
        (result / "console.log").write_text("complete", encoding="utf-8")

    @staticmethod
    def _write_target_result(result: Path) -> None:
        write_json(result / "target-metrics.json", {"max_cpu_percent": 50})
        (result / "target-samples.tsv").write_text("timestamp\tcpu\n1\t50\n", encoding="utf-8")
        (result / "admission.jsonl").write_text("", encoding="utf-8")
        write_json(result / "admission-summary.json", {"records": 0})
        (result / "endpoint.log").write_text("complete", encoding="utf-8")


class IsolationInfrastructure:
    search_target_name = "target"
    search_target_zone = "test-zone"

    def ssh_output(self, _name: str, _zone: str, command: str) -> str:
        self.command = command
        raise LoadTestError("endpoint logs unavailable")


class DeploymentConfigInfrastructure:
    search_target_name = "target"
    search_target_zone = "test-zone"

    def ssh_output(self, _name: str, _zone: str, command: str) -> str:
        self.command = command
        return "MAX_CONCURRENCY=2\nINFERENCE_THREADS=1\n"

    def compute_output(self, *_arguments: str) -> str:
        return "e2-standard-4"


class TestSearchRunConfig(unittest.TestCase):
    def test_duration_units_and_vu_calculation(self) -> None:
        self.assertAlmostEqual(duration_seconds("500ms"), 0.5)
        self.assertAlmostEqual(duration_seconds("2m"), 120.0)
        config = SearchRunConfig(rate=3, client_timeout_seconds=15).validated()
        self.assertEqual(config.pre_allocated_vus, 45)
        self.assertEqual(config.max_vus, 45)

    def test_rejects_zero_duration_and_vus(self) -> None:
        with self.assertRaisesRegex(LoadTestError, "greater than zero"):
            duration_seconds("0s")
        with self.assertRaisesRegex(LoadTestError, "positive integers"):
            SearchRunConfig(rate=1, pre_allocated_vus=0).validated()

    def test_rejects_insufficient_preallocated_vus(self) -> None:
        with self.assertRaisesRegex(LoadTestError, "45 <= 44 <= 44"):
            SearchRunConfig(rate=3, pre_allocated_vus=44).validated()

    def test_trace_namespace_produces_valid_request_uuid(self) -> None:
        namespace = SearchEmbeddingSession._trace_namespace("test-run")
        trace_id = f"{namespace}-000000000001"
        self.assertEqual(str(UUID(trace_id)), trace_id)


class TestCli(unittest.TestCase):
    def test_defaults_match_diagnostic_plan(self) -> None:
        arguments = build_parser().parse_args(
            ["search-embedding-run", "--rate", "2"]
        )
        config = search_run_config(arguments)
        self.assertEqual(config.duration, "2m")
        self.assertEqual(config.client_timeout_seconds, 15)
        self.assertEqual(config.pre_allocated_vus, 30)

    def test_invalid_rate_fails_before_cloud_setup(self) -> None:
        arguments = build_parser().parse_args(
            ["search-embedding-run", "--rate", "0"]
        )
        with self.assertRaisesRegex(LoadTestError, "positive integer"):
            search_run_config(arguments)


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
                    ScenarioRequest(scenario="smoke.js", target_url="https://example.test")
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


class TestArtifacts(unittest.TestCase):
    def test_partial_result_retry_does_not_create_nested_scenario(self) -> None:
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
            for name in ("summary.json", "raw.json.gz", "console.log", "runner-metrics.json"):
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

    def test_search_acceptance_requires_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = settings_for(Path(temporary_directory))
            manager = ArtifactManager(settings, cast(Infrastructure, object()))
            result = settings.artifact_root / "test-run/search-embedding"
            write_json(result / "metadata.json", {"acceptance": {"accepted": True}})
            write_json(
                result / "target-vm/target-metrics.json",
                {
                    "vm_restart_detected": False,
                    "container_restart_detected": False,
                    "container_running_at_end": True,
                    "file_descriptor_error_detected": False,
                    "oom_event_detected": False,
                },
            )
            write_json(
                result / "target-vm/admission-summary.json",
                {
                    "records": 2,
                    "foreign_workload_records": 0,
                    "model_version_matches": True,
                },
            )
            manager.merge_search_metadata("test-run", recovered=False)
            metadata = json.loads((result / "metadata.json").read_text(encoding="utf-8"))
            self.assertFalse(metadata["search_acceptance"]["accepted"])


class TestReviewRegressions(unittest.TestCase):
    def test_custom_k6_inputs_do_not_use_reserved_prefix(self) -> None:
        scenario = (SCRIPT_DIR.parents[1] / "load-tests/k6/scenarios/search-embedding.js").read_text(
            encoding="utf-8"
        )
        executor = (SCRIPT_DIR / "remote/k6-executor.sh").read_text(encoding="utf-8")
        self.assertNotIn("__ENV.K6_", scenario)
        self.assertNotIn("export K6_", executor)
        self.assertIn("__ENV.LT_DURATION", scenario)

    def test_isolation_check_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fake = IsolationInfrastructure()
            target = SearchTarget(
                settings_for(Path(temporary_directory)),
                cast(Infrastructure, fake),
                cast(K6Runner, object()),
            )
            with self.assertRaisesRegex(LoadTestError, "logs unavailable"):
                target.assert_no_recent_requests()
            self.assertIn("set -euo pipefail", fake.command)
            self.assertNotIn("2>/dev/null", fake.command)

    def test_deployment_config_collects_inference_threads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fake = DeploymentConfigInfrastructure()
            target = SearchTarget(
                settings_for(Path(temporary_directory)),
                cast(Infrastructure, fake),
                cast(K6Runner, object()),
            )

            config = target._deployment_config()

            self.assertEqual(config["INFERENCE_THREADS"], "1")
            self.assertIn('$1 == "INFERENCE_THREADS"', fake.command)

    def test_missing_raw_output_does_not_abort_remote_cleanup(self) -> None:
        executor = (SCRIPT_DIR / "remote/k6-executor.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ -f "$raw_path" ]]; then', executor)
        self.assertNotIn('[[ -f "$raw_path" ]] && gzip', executor)

    def test_recovery_probe_is_not_counted_as_load(self) -> None:
        evidence = (SCRIPT_DIR / "remote/target-evidence.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('!= $recovery_trace_id', evidence)

    def test_settings_has_no_stale_project_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            self.assertFalse(hasattr(settings_for(Path(temporary_directory)), "project_id"))


if __name__ == "__main__":
    unittest.main()
