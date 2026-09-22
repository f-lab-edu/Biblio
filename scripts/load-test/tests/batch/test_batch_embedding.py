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

from batch_embedding import BatchEmbeddingSession, BatchRunConfig
from infrastructure import CommandRunner, Infrastructure, LoadTestError
from k6_runner import ArtifactManager, K6Runner
from runner import batch_run_config, build_parser
from tests.helpers import DownloadInfrastructure, FakeInfrastructure, settings_for, write_json


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
            write_json(
                data_dir / names["db_profile_sha256"],
                {"observed_mix": observed_mix},
            )
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


class TestBatchCli(unittest.TestCase):
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


class TestBatchArtifacts(unittest.TestCase):
    def test_stress_summary_separates_initial_and_retry_requests(self) -> None:
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

    def test_stable_presets_reject_any_initial_503(self) -> None:
        stress_metrics = {
            "initial_503": 1,
            "retry_exhausted": 0,
            "client_errors": 0,
            "unexpected_statuses": 0,
            "invalid_responses": 0,
        }

        self.assertFalse(ArtifactManager._batch_client_accepted(stress_metrics, "S3"))
        self.assertTrue(ArtifactManager._batch_client_accepted(stress_metrics, "S4"))

    def test_target_result_uses_requested_target_and_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = settings_for(root)
            fake = DownloadInfrastructure(target_download=True)
            manager = ArtifactManager(settings, cast(Infrastructure, fake))
            local = settings.artifact_root / "batch-embedding/test-run"
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


class TestBatchEnvironment(unittest.TestCase):
    def test_environment_is_forwarded_by_runner(self) -> None:
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
        executor = (LOAD_TEST_DIR / "remote/k6-executor.sh").read_text(
            encoding="utf-8"
        )

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
