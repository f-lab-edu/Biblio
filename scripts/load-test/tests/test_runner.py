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

from batch_embedding import BatchEmbeddingSession, BatchRunConfig
from infrastructure import (
    CommandRunner,
    Infrastructure,
    JsonState,
    LoadTestError,
    Settings,
)
from k6_runner import ArtifactManager, K6Runner, ScenarioRequest
from runner import batch_run_config, build_parser, search_run_config
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
        self.last_source = _source
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


class TestBatchRunConfig(unittest.TestCase):
    def test_capacity_defaults_match_function_test_plan(self) -> None:
        config = BatchRunConfig(scenario="capacity").validated()

        self.assertEqual(config.batch_size, 4)
        self.assertEqual(config.vus, 1)
        self.assertEqual(config.duration, "2m")
        self.assertEqual(config.client_timeout_seconds, 180)
        self.assertEqual(config.retry_profile, "raw")
        self.assertEqual(config.response_verification, "none")

    def test_stress_presets_fix_current_worker_limits(self) -> None:
        expected = {
            "S1": (1, "2m", "raw"),
            "S2": (4, "10m", "raw"),
            "S3": (4, "30m", "worker-client"),
            "S4": (5, "5m", "worker-client"),
        }

        for preset, values in expected.items():
            config = BatchRunConfig.stress(preset)
            self.assertEqual((config.vus, config.duration, config.retry_profile), values)
            self.assertEqual(config.input_set, "observed-mix")
            self.assertEqual(config.batch_size, 4)
            self.assertEqual(config.response_verification, "sampled")
            self.assertEqual(config.graceful_stop, "4m")

    def test_rejects_bucket_from_the_other_input_set(self) -> None:
        with self.assertRaisesRegex(LoadTestError, "invalid for capacity"):
            BatchRunConfig(
                scenario="capacity",
                input_set="capacity",
                input_bucket="observed_tail",
            ).validated()

    def test_rejects_batch_above_live_request_limit(self) -> None:
        with self.assertRaisesRegex(LoadTestError, "cannot exceed"):
            BatchRunConfig(scenario="capacity", batch_size=33).validated()

    def test_fixture_hash_validation_detects_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = settings_for(Path(temporary_directory))
            data_dir = settings.load_test_root / "data"
            data_dir.mkdir(parents=True)
            names = {
                "fixture_sha256": "batch-embedding-enriched-texts.json",
                "truncation_fixture_sha256": "batch-embedding-truncation-inputs.json",
                "boundary_fixture_sha256": "batch-embedding-boundary-inputs.json",
                "db_profile_sha256": "batch-embedding-db-profile.json",
            }
            for filename in names.values():
                (data_dir / filename).write_text(filename, encoding="utf-8")
            observed_mix = {
                "source": "all_rows",
                "sample_count": 7,
                "effective_token_limit": 512,
                "raw_token_bucket_counts": {
                    "short": 1,
                    "medium": 1,
                    "long": 1,
                    "xlong": 1,
                    "boundary": 1,
                    "over_limit": 1,
                    "observed_tail": 1,
                },
                "raw_text_persisted": False,
            }
            write_json(data_dir / names["db_profile_sha256"], {"observed_mix": observed_mix})
            session = object.__new__(BatchEmbeddingSession)
            session.settings = settings
            manifest = {
                "target_model_version": "bge-m3-base",
                "db_profile": {"observed_mix": observed_mix},
                "hashes": {
                    key: session._sha256(data_dir / filename)
                    for key, filename in names.items()
                },
            }

            session._validated_fixture_hashes(manifest, "bge-m3-base")
            (data_dir / names["fixture_sha256"]).write_text(
                "changed", encoding="utf-8"
            )

            with self.assertRaisesRegex(LoadTestError, "hash mismatch"):
                session._validated_fixture_hashes(manifest, "bge-m3-base")


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

    def test_builds_batch_capacity_config(self) -> None:
        arguments = build_parser().parse_args(
            [
                "batch-embedding-run",
                "--scenario",
                "capacity",
                "--batch-size",
                "4",
                "--vus",
                "2",
            ]
        )

        config = batch_run_config(arguments)

        self.assertEqual(config.batch_size, 4)
        self.assertEqual(config.vus, 2)

    def test_parses_batch_stress_preset(self) -> None:
        arguments = build_parser().parse_args(
            ["batch-embedding-stress-run", "--preset", "S2"]
        )

        config = BatchRunConfig.stress(arguments.preset)

        self.assertEqual(config.vus, 4)
        self.assertEqual(config.batch_size, 4)
        self.assertEqual(config.duration, "10m")

    def test_rejects_removed_worker_shape_scenario(self) -> None:
        with self.assertRaisesRegex(LoadTestError, "must be capacity"):
            BatchRunConfig(scenario="worker-shape").validated()

    def test_stress_guard_rejects_worker_scale_out(self) -> None:
        session = object.__new__(BatchEmbeddingSession)
        session._worker_deployment_snapshot = lambda: {
            "max_instance_count": 2,
            "worker_concurrency": 4,
            "embedding_batch_size": 4,
            "embedding_timeout_sec": 180,
        }

        with self.assertRaisesRegex(LoadTestError, "max_instance_count=2"):
            session._assert_stress_worker_limits(BatchRunConfig.stress("S2"))


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
    def test_batch_stress_summary_separates_initial_and_retry_requests(self) -> None:
        summary = {
            "metrics": {
                "batch_embedding_initial_requests": {"count": 10},
                "batch_embedding_initial_texts": {"count": 40},
                "batch_embedding_successful_texts": {"rate": 2.0},
                "batch_embedding_retry_requests": {"count": 2},
                "batch_embedding_initial_503": {"count": 2},
                "batch_embedding_retry_success": {"count": 2},
                "batch_embedding_first_window_successful_texts": {"count": 540},
                "batch_embedding_first_window_logical_duration": {"p(95)": 21000},
                "batch_embedding_first_window_status_503": {"count": 2},
            }
        }

        result = ArtifactManager._batch_stress_metrics(summary, "30m")

        self.assertAlmostEqual(result["retry_amplification"], 1.2)
        self.assertEqual(result["retry_requests"], 2)
        self.assertAlmostEqual(
            result["windows"]["first"]["successful_texts_per_second"], 1.8
        )
        self.assertEqual(result["windows"]["first"]["logical_duration_p95_ms"], 21000)
        self.assertEqual(result["initial_503"], 2)
        self.assertFalse(ArtifactManager._batch_client_accepted(result, "S2"))

    def test_metric_value_supports_nested_summary_values(self) -> None:
        metrics = {"dropped_iterations": {"values": {"count": 3}}}

        result = ArtifactManager._metric_value(
            metrics, "dropped_iterations", "count"
        )

        self.assertAlmostEqual(result, 3.0)

    def test_stable_stress_presets_reject_any_initial_503(self) -> None:
        stress_metrics = {
            "initial_503": 1,
            "retry_exhausted": 0,
            "client_errors": 0,
            "unexpected_statuses": 0,
            "invalid_responses": 0,
        }

        self.assertFalse(ArtifactManager._batch_client_accepted(stress_metrics, "S3"))
        self.assertTrue(ArtifactManager._batch_client_accepted(stress_metrics, "S4"))

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

    def test_batch_target_result_uses_requested_target_and_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = settings_for(root)
            fake = DownloadInfrastructure(target_download=True)
            manager = ArtifactManager(settings, cast(Infrastructure, fake))
            local = settings.artifact_root / "test-run/batch-embedding-capacity"
            local.mkdir(parents=True)

            result = manager.collect_target_results(
                "test-run",
                "batch-embedding-capacity",
                target_name="batch-target",
                target_zone="batch-zone",
            )

            self.assertEqual(fake.last_zone, "batch-zone")
            self.assertTrue(fake.last_source.startswith("batch-target:"))
            self.assertTrue((result / "target-metrics.json").is_file())

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

    def test_batch_environment_is_forwarded_by_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = settings_for(Path(temporary_directory))
            runner = K6Runner(
                settings,
                CommandRunner(),
                cast(Infrastructure, FakeInfrastructure()),
                cast(ArtifactManager, object()),
            )
            environment = {
                "TARGET_URL": "https://embedding.example.test/embed",
                "MODEL_VERSION": "bge-m3-base",
                "BATCH_SIZE": "4",
                "LT_VUS": "2",
                "INPUT_BUCKET": "boundary",
            }
            with patch.dict(os.environ, environment, clear=True):
                request = runner.request_from_environment(
                    "scenarios/batch-embedding-capacity.js"
                )

            self.assertEqual(request.load_environment["BATCH_SIZE"], "4")
            self.assertEqual(request.load_environment["LT_VUS"], "2")
            self.assertEqual(request.load_environment["INPUT_BUCKET"], "boundary")

    def test_stress_environment_and_metadata_are_forwarded(self) -> None:
        config = BatchRunConfig.stress("S3")
        environment = BatchEmbeddingSession._load_environment(
            {"model_version": "bge-m3-base"},
            config,
            "01234567-89ab-4cde-8fab",
        )

        self.assertEqual(environment["RETRY_PROFILE"], "worker-client")
        self.assertEqual(environment["RESPONSE_VERIFICATION"], "sampled")
        self.assertEqual(environment["LT_GRACEFUL_STOP"], "4m")

    def test_remote_executor_exports_batch_environment(self) -> None:
        executor = (SCRIPT_DIR / "remote/k6-executor.sh").read_text(encoding="utf-8")

        for name in (
            "LT_VUS",
            "BATCH_SIZE",
            "INPUT_SET",
            "RESPONSE_VERIFICATION",
            "RETRY_PROFILE",
            "RETRY_SEED",
            "LT_GRACEFUL_STOP",
        ):
            self.assertIn(f'export {name}="', executor)

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
