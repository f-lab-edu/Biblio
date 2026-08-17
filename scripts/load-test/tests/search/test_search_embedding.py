from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast
from uuid import UUID

LOAD_TEST_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOAD_TEST_DIR))

from infrastructure import Infrastructure, LoadTestError
from k6_runner import ArtifactManager, K6Runner
from load_config import duration_seconds
from runner import build_parser, search_run_config
from search_embedding import SearchEmbeddingSession, SearchRunConfig, SearchTarget
from tests.helpers import (
    DeploymentConfigInfrastructure,
    IsolationInfrastructure,
    settings_for,
    write_json,
)


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


class TestSearchCli(unittest.TestCase):
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


class TestSearchArtifacts(unittest.TestCase):
    def test_search_acceptance_requires_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = settings_for(Path(temporary_directory))
            manager = ArtifactManager(settings, cast(Infrastructure, object()))
            result = settings.artifact_root / "search-embedding/test-run"
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


class TestSearchTarget(unittest.TestCase):
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


class TestSearchRegressions(unittest.TestCase):
    def test_custom_k6_inputs_do_not_use_reserved_prefix(self) -> None:
        scenario = (
            LOAD_TEST_DIR.parents[1] / "load-tests/k6/scenarios/search-embedding.js"
        ).read_text(encoding="utf-8")
        executor = (LOAD_TEST_DIR / "remote/k6-executor.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("__ENV.K6_", scenario)
        self.assertNotIn("export K6_", executor)
        self.assertIn("__ENV.LT_DURATION", scenario)

    def test_recovery_probe_is_not_counted_as_load(self) -> None:
        evidence = (LOAD_TEST_DIR / "remote/target-evidence.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("!= $recovery_trace_id", evidence)
