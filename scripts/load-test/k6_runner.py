from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from infrastructure import (
    CommandRunner,
    Infrastructure,
    JsonState,
    LoadTestError,
    Settings,
)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact_utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _required_file(path: Path, *, allow_empty: bool = False) -> None:
    if not path.is_file() or (not allow_empty and path.stat().st_size == 0):
        raise LoadTestError(f"Collected result is missing or empty: {path}")


class ArtifactManager:
    """Collects remote outputs and computes local acceptance metadata."""

    def __init__(self, settings: Settings, infrastructure: Infrastructure) -> None:
        self.settings = settings
        self.infrastructure = infrastructure
        self.run_state = JsonState(settings.run_state_file)

    def collect_runner_results(self) -> Path:
        state = self.run_state.read()
        run_id = str(state["run_id"])
        scenario = str(state["scenario"])
        local_parent = self.settings.artifact_root / run_id
        local_dir = local_parent / scenario
        local_parent.mkdir(parents=True, exist_ok=True)
        required_names = (
            "summary.json",
            "raw.json.gz",
            "console.log",
            "runner-metrics.json",
        )
        if not self._has_required_files(local_dir, required_names):
            self._download_runner_result(state, local_parent, local_dir, scenario)
        for name in required_names:
            _required_file(local_dir / name)
        self._write_runner_acceptance(local_dir, state)
        print(f"Results collected at {local_dir}")
        return local_dir

    def _download_runner_result(
        self,
        state: dict[str, Any],
        local_parent: Path,
        local_dir: Path,
        scenario: str,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".collect-", dir=local_parent
        ) as temporary_directory:
            temporary_root = Path(temporary_directory)
            self.infrastructure.scp(
                f"{self.infrastructure.runner_name}:{state['remote_result']}",
                f"{temporary_root}/",
                zone=self.infrastructure.runner_zone,
                recursive=True,
            )
            downloaded_dir = temporary_root / scenario
            if not downloaded_dir.is_dir():
                raise LoadTestError(
                    f"Downloaded result directory is missing: {downloaded_dir}"
                )
            shutil.copytree(downloaded_dir, local_dir, dirs_exist_ok=True)

    def _write_runner_acceptance(self, local_dir: Path, state: dict[str, Any]) -> None:
        summary = self._read_object(local_dir / "summary.json")
        runner_metrics = self._read_object(local_dir / "runner-metrics.json")
        max_cpu = self._number(runner_metrics, "max_cpu_percent")
        max_memory = self._number(runner_metrics, "max_memory_percent")
        summary_metrics = summary.get("metrics", {})
        dropped_iterations = (
            self._metric_value(summary_metrics, "dropped_iterations", "count")
            if isinstance(summary_metrics, dict)
            else 0
        )
        accepted = self._runner_accepted(
            runner_metrics, max_cpu, max_memory, dropped_iterations
        )
        metadata = {
            **state,
            "collected_at": utc_timestamp(),
            "runner_metrics": runner_metrics,
            "dropped_iterations": dropped_iterations,
            "acceptance": {
                "cpu_below_80_percent": max_cpu < 80,
                "memory_below_90_percent": max_memory < 90,
                "dropped_iterations_zero": dropped_iterations == 0,
                "network_not_saturated": not runner_metrics.get(
                    "network_saturation_detected", False
                ),
                "no_file_descriptor_errors": not runner_metrics.get(
                    "file_descriptor_error_detected", False
                ),
                "no_vm_restart": not runner_metrics.get("vm_restart_detected", False),
                "accepted": accepted,
            },
        }
        stress_metrics = self._batch_stress_metrics(
            summary, str(state.get("load_config", {}).get("duration", "0s"))
        )
        if stress_metrics:
            metadata["batch_stress_metrics"] = stress_metrics
        self._write_object(local_dir / "metadata.json", metadata)

    @classmethod
    def _batch_stress_metrics(
        cls, summary: dict[str, Any], duration: str = "0s"
    ) -> dict[str, Any]:
        metrics = summary.get("metrics", {})
        if not isinstance(metrics, dict) or "batch_embedding_initial_requests" not in metrics:
            return {}
        initial_requests = cls._metric_value(metrics, "batch_embedding_initial_requests", "count")
        retry_requests = cls._metric_value(metrics, "batch_embedding_retry_requests", "count")
        window_seconds = min(300.0, cls._duration_seconds(duration))
        windows = {}
        for window in ("first", "middle", "last"):
            successful_texts = cls._metric_value(
                metrics,
                f"batch_embedding_{window}_window_successful_texts",
                "count",
            )
            windows[window] = {
                "successful_texts": successful_texts,
                "successful_texts_per_second": (
                    successful_texts / window_seconds if window_seconds > 0 else 0
                ),
                "logical_duration_p95_ms": cls._metric_value(
                    metrics,
                    f"batch_embedding_{window}_window_logical_duration",
                    "p(95)",
                ),
                "status_503_count": cls._metric_value(
                    metrics,
                    f"batch_embedding_{window}_window_status_503",
                    "count",
                ),
            }
        return {
            "initial_requests": initial_requests,
            "initial_texts": cls._metric_value(
                metrics, "batch_embedding_initial_texts", "count"
            ),
            "successful_texts_per_second": cls._metric_value(
                metrics, "batch_embedding_successful_texts", "rate"
            ),
            "retry_requests": retry_requests,
            "retry_amplification": (
                (initial_requests + retry_requests) / initial_requests
                if initial_requests > 0
                else 0
            ),
            "initial_503": cls._metric_value(
                metrics, "batch_embedding_initial_503", "count"
            ),
            "retry_success": cls._metric_value(
                metrics, "batch_embedding_retry_success", "count"
            ),
            "retry_exhausted": cls._metric_value(
                metrics, "batch_embedding_retry_exhausted", "count"
            ),
            "client_errors": cls._metric_value(
                metrics, "batch_embedding_client_error", "count"
            ),
            "unexpected_statuses": cls._metric_value(
                metrics, "batch_embedding_unexpected_status", "count"
            ),
            "invalid_responses": cls._metric_value(
                metrics, "batch_embedding_invalid_response", "count"
            ),
            "attempt_counts": {
                str(attempt): cls._metric_value(
                    metrics, f"batch_embedding_attempt_{attempt}", "count"
                )
                for attempt in range(1, 5)
            },
            "input_texts_by_bucket": {
                bucket: cls._metric_value(
                    metrics, f"batch_embedding_input_{bucket}_texts", "count"
                )
                for bucket in (
                    "short",
                    "medium",
                    "long",
                    "xlong",
                    "boundary",
                    "over_limit",
                    "observed_tail",
                )
            },
            "payload_bytes_p95": cls._metric_value(
                metrics, "batch_embedding_payload_bytes", "p(95)"
            ),
            "logical_duration_p95_ms": cls._metric_value(
                metrics, "batch_embedding_logical_duration", "p(95)"
            ),
            "windows": windows,
        }

    @staticmethod
    def _duration_seconds(value: str) -> float:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m)", value)
        if not match:
            return 0
        number = float(match.group(1))
        return number / 1000 if match.group(2) == "ms" else (
            number * 60 if match.group(2) == "m" else number
        )

    @staticmethod
    def _metric_value(
        metrics: dict[str, Any], metric_name: str, value_name: str
    ) -> float:
        metric = metrics.get(metric_name, {})
        if not isinstance(metric, dict):
            return 0.0
        value = metric.get(value_name)
        if not isinstance(value, (int, float)):
            nested_values = metric.get("values", {})
            value = (
                nested_values.get(value_name, 0)
                if isinstance(nested_values, dict)
                else 0
            )
        return float(value) if isinstance(value, (int, float)) else 0.0

    @staticmethod
    def _runner_accepted(
        metrics: dict[str, Any], max_cpu: float, max_memory: float, dropped: Any
    ) -> bool:
        return (
            max_cpu < 80
            and max_memory < 90
            and dropped == 0
            and not metrics.get("network_saturation_detected", False)
            and not metrics.get("file_descriptor_error_detected", False)
            and not metrics.get("vm_restart_detected", False)
        )

    def collect_target_results(
        self,
        run_id: str,
        scenario: str = "search-embedding",
        *,
        target_name: str | None = None,
        target_zone: str | None = None,
    ) -> Path:
        local_dir = self.settings.artifact_root / run_id / scenario
        target_dir = local_dir / "target-vm"
        required_names = (
            "target-metrics.json",
            "target-samples.tsv",
            "admission.jsonl",
            "admission-summary.json",
            "endpoint.log",
        )
        if not local_dir.is_dir():
            raise LoadTestError(f"Local k6 result directory is missing: {local_dir}")
        if not self._has_required_files(
            target_dir, required_names, allow_empty=frozenset({"admission.jsonl"})
        ):
            self._download_target_result(
                run_id,
                local_dir,
                target_dir,
                target_name or self.infrastructure.search_target_name,
                target_zone or self.infrastructure.search_target_zone,
            )
        for name in required_names:
            _required_file(target_dir / name, allow_empty=name == "admission.jsonl")
        return target_dir

    def _download_target_result(
        self,
        run_id: str,
        local_dir: Path,
        target_dir: Path,
        target_name: str,
        target_zone: str,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".target-collect-", dir=local_dir
        ) as temporary_directory:
            temporary_root = Path(temporary_directory)
            self.infrastructure.scp(
                f"{target_name}:~/biblio-target-load-results/{run_id}",
                f"{temporary_root}/",
                zone=target_zone,
                recursive=True,
            )
            downloaded_dir = temporary_root / run_id
            if not downloaded_dir.is_dir():
                raise LoadTestError(
                    f"Downloaded target result directory is missing: {downloaded_dir}"
                )
            shutil.copytree(downloaded_dir, target_dir, dirs_exist_ok=True)

    def merge_search_metadata(self, run_id: str, recovered: bool) -> None:
        self.merge_embedding_metadata(
            run_id,
            "search-embedding",
            recovered,
            acceptance_key="search_acceptance",
        )

    def merge_embedding_metadata(
        self,
        run_id: str,
        scenario: str,
        recovered: bool,
        *,
        acceptance_key: str,
    ) -> None:
        local_dir = self.settings.artifact_root / run_id / scenario
        metadata_path = local_dir / "metadata.json"
        metadata = self._read_object(metadata_path)
        target_metrics = self._read_object(local_dir / "target-vm/target-metrics.json")
        admission = self._read_object(local_dir / "target-vm/admission-summary.json")
        runner_accepted = bool(metadata.get("acceptance", {}).get("accepted"))
        stress_metrics = metadata.get("batch_stress_metrics", {})
        stress_preset = metadata.get("stress_preset", "not-set")
        client_accepted = self._batch_client_accepted(stress_metrics, stress_preset)
        embedding_accepted = self._search_accepted(
            runner_accepted, target_metrics, admission, recovered, client_accepted
        )
        metadata.update(
            {
                "target_metrics": target_metrics,
                "admission": admission,
                acceptance_key: self._search_acceptance_details(
                    target_metrics,
                    admission,
                    recovered,
                    embedding_accepted,
                    client_accepted,
                ),
            }
        )
        self._write_object(metadata_path, metadata)

    @staticmethod
    def _batch_client_accepted(
        stress_metrics: object, stress_preset: object
    ) -> bool:
        if not isinstance(stress_metrics, dict) or not stress_metrics:
            return True
        errors_absent = all(
            stress_metrics.get(name, 0) == 0
            for name in (
                "retry_exhausted",
                "client_errors",
                "unexpected_statuses",
                "invalid_responses",
            )
        )
        stable_preset_has_503 = (
            stress_preset in {"S1", "S2", "S3"}
            and stress_metrics.get("initial_503", 0) > 0
        )
        return errors_absent and not stable_preset_has_503

    @staticmethod
    def _search_accepted(
        runner_accepted: bool,
        target: dict[str, Any],
        admission: dict[str, Any],
        recovered: bool,
        client_accepted: bool = True,
    ) -> bool:
        return (
            runner_accepted
            and admission.get("records", 0) > 0
            and admission.get("foreign_workload_records") == 0
            and not target.get("vm_restart_detected", False)
            and not target.get("container_restart_detected", False)
            and bool(target.get("container_running_at_end"))
            and not target.get("file_descriptor_error_detected", False)
            and not target.get("oom_event_detected", False)
            and bool(admission.get("model_version_matches"))
            and recovered
            and client_accepted
        )

    @staticmethod
    def _search_acceptance_details(
        target: dict[str, Any],
        admission: dict[str, Any],
        recovered: bool,
        accepted: bool,
        client_accepted: bool = True,
    ) -> dict[str, bool]:
        return {
            "admission_records_present": admission.get("records", 0) > 0,
            "no_foreign_workload": admission.get("foreign_workload_records") == 0,
            "target_vm_not_restarted": not target.get("vm_restart_detected", False),
            "target_container_not_restarted": not target.get(
                "container_restart_detected", False
            ),
            "target_container_running": bool(target.get("container_running_at_end")),
            "no_target_fd_errors": not target.get(
                "file_descriptor_error_detected", False
            ),
            "no_target_oom": not target.get("oom_event_detected", False),
            "model_version_unchanged": bool(admission.get("model_version_matches")),
            "recovered": recovered,
            "client_results_valid": client_accepted,
            "accepted": accepted,
        }

    @staticmethod
    def _has_required_files(
        directory: Path,
        names: tuple[str, ...],
        *,
        allow_empty: frozenset[str] = frozenset(),
    ) -> bool:
        return directory.is_dir() and all(
            (directory / name).is_file()
            and (name in allow_empty or (directory / name).stat().st_size > 0)
            for name in names
        )

    @staticmethod
    def _read_object(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LoadTestError(f"Invalid JSON result: {path}") from error
        if not isinstance(value, dict):
            raise LoadTestError(f"JSON result must be an object: {path}")
        return value

    @staticmethod
    def _number(value: dict[str, Any], key: str) -> float:
        number = value.get(key)
        if not isinstance(number, (int, float)):
            raise LoadTestError(f"runner-metrics.json has invalid {key}.")
        return float(number)

    @staticmethod
    def _write_object(path: Path, value: dict[str, Any]) -> None:
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        temporary_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(path)


@dataclass(frozen=True)
class ScenarioRequest:
    scenario: str
    target_url: str
    iam_audience: str = ""
    expected_status: str = "200"
    target_config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    load_environment: dict[str, str] = field(default_factory=dict)
    run_id: str = ""
    keep_runner_running: bool = False


class K6Runner:
    """Deploys k6 sources, invokes the remote executor, and collects results."""

    REMOTE_EXECUTOR = "biblio-k6-executor.sh"

    def __init__(
        self,
        settings: Settings,
        commands: CommandRunner,
        infrastructure: Infrastructure,
        artifacts: ArtifactManager,
    ) -> None:
        self.settings = settings
        self.commands = commands
        self.infrastructure = infrastructure
        self.artifacts = artifacts
        self.sync_state = JsonState(settings.sync_state_file)
        self.run_state = JsonState(settings.run_state_file)

    def sync_sources(self) -> None:
        if self.infrastructure.runner_status() != "RUNNING":
            raise LoadTestError("Runner must be RUNNING before sync.")
        self.commands.require("git")
        git_sha = self.commands.output(
            ["git", "-C", str(self.settings.repo_root), "rev-parse", "HEAD"]
        )
        remote_root = f"biblio-load-test-{git_sha[:12]}-{compact_utc_timestamp()}"
        self.infrastructure.scp(
            str(self.settings.load_test_root),
            f"{self.infrastructure.runner_name}:~/{remote_root}",
            zone=self.infrastructure.runner_zone,
            recursive=True,
        )
        self.infrastructure.scp(
            str(self.settings.remote_k6_executor),
            f"{self.infrastructure.runner_name}:~/{self.REMOTE_EXECUTOR}",
            zone=self.infrastructure.runner_zone,
        )
        self.sync_state.write({"git_sha": git_sha, "remote_root": remote_root})

    def run_scenario(self, request: ScenarioRequest) -> Path:
        try:
            scenario_path = self._validate_scenario(request)
            sync_state = self.sync_state.read()
            run_id = request.run_id or compact_utc_timestamp()
            if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
                raise LoadTestError("Run ID contains unsafe characters.")
            scenario_slug = scenario_path.stem
            state = self._build_run_state(request, sync_state, run_id, scenario_slug)
            self.run_state.write(state)
            remote_status = self._execute_remote(request, sync_state, run_id, scenario_slug)
            local_dir = self.artifacts.collect_runner_results()
            if remote_status != 0:
                raise LoadTestError(f"k6 exited with status {remote_status}.")
            return local_dir
        finally:
            if not request.keep_runner_running:
                self._stop_runner_after_run()

    def run_from_environment(self, scenario: str) -> Path:
        try:
            return self.run_scenario(self.request_from_environment(scenario))
        except LoadTestError:
            self._stop_runner_after_run()
            raise

    def collect_latest(self) -> Path:
        started_for_collect = self.infrastructure.runner_status() == "TERMINATED"
        if started_for_collect:
            self.infrastructure.start_runner()
        try:
            return self.artifacts.collect_runner_results()
        finally:
            if started_for_collect:
                self.infrastructure.stop_runner()

    def smoke(self) -> Path:
        service_url = self.infrastructure.terraform_output("search_service_url")
        target_url = os.environ.get("TARGET_URL", f"{service_url.rstrip('/')}/health")
        target_config = self._environment_json(
            "TARGET_CONFIG_JSON",
            {
                "service": "search-service",
                "endpoint": "/health",
                "authentication": "cloud-run-iam",
            },
        )
        self.infrastructure.start_runner()
        try:
            self.sync_sources()
            return self.run_scenario(
                ScenarioRequest(
                    scenario="smoke.js",
                    target_url=target_url,
                    iam_audience=os.environ.get("IAM_AUDIENCE", service_url),
                    expected_status=os.environ.get("EXPECTED_STATUS", "200"),
                    target_config=target_config,
                    metadata={"load_profile": "1 VU / 10s"},
                )
            )
        except LoadTestError:
            self._stop_runner_after_run()
            raise

    def request_from_environment(self, scenario: str) -> ScenarioRequest:
        target_url = os.environ.get("TARGET_URL", "")
        if not target_url:
            raise LoadTestError("TARGET_URL is required.")
        metadata_names = (
            "CORPUS_CHUNK_COUNT",
            "LOAD_PROFILE",
            "QUERY_SET_HASH",
            "FIXTURE_HASH",
            "TRUNCATION_FIXTURE_HASH",
            "FIXTURE_MANIFEST_HASH",
            "CORPUS_MANIFEST_HASH",
            "INPUT_PROFILE",
            "RETRY_PROFILE",
            "STRESS_PRESET",
        )
        load_environment_names = (
            "MODEL_VERSION",
            "LT_RATE",
            "LT_TIME_UNIT",
            "LT_DURATION",
            "LT_CLIENT_TIMEOUT_SECONDS",
            "LT_PRE_ALLOCATED_VUS",
            "LT_MAX_VUS",
            "TRACE_ID_NAMESPACE",
            "LT_VUS",
            "BATCH_SIZE",
            "INPUT_SET",
            "INPUT_BUCKET",
            "CONTENT_PROFILE",
            "VERIFY_RESPONSE",
            "RESPONSE_VERIFICATION",
            "RETRY_PROFILE",
            "RETRY_SEED",
            "LT_GRACEFUL_STOP",
        )
        return ScenarioRequest(
            scenario=scenario,
            target_url=target_url,
            iam_audience=os.environ.get("IAM_AUDIENCE", ""),
            expected_status=os.environ.get("EXPECTED_STATUS", "200"),
            target_config=self._environment_json("TARGET_CONFIG_JSON", {}),
            metadata={name.lower(): os.environ.get(name, "not-set") for name in metadata_names},
            load_environment={name: os.environ.get(name, "") for name in load_environment_names},
        )

    def _validate_scenario(self, request: ScenarioRequest) -> Path:
        scenario_path = Path(request.scenario)
        if scenario_path.is_absolute() or ".." in scenario_path.parts or scenario_path.suffix != ".js":
            raise LoadTestError("Scenario must be a relative .js path without .. segments.")
        full_path = self.settings.load_test_root / scenario_path
        if not full_path.is_file():
            raise LoadTestError(f"Scenario not found: {full_path}")
        if not request.target_url:
            raise LoadTestError("TARGET_URL is required.")
        return scenario_path

    def _build_run_state(
        self,
        request: ScenarioRequest,
        sync_state: dict[str, Any],
        run_id: str,
        scenario_slug: str,
    ) -> dict[str, Any]:
        environment = request.load_environment
        metadata = request.metadata
        return {
            "run_id": run_id,
            "scenario": scenario_slug,
            "remote_result": f"~/biblio-load-results/{run_id}/{scenario_slug}",
            "git_sha": sync_state["git_sha"],
            "k6_version": self.infrastructure.ssh_output(
                self.infrastructure.runner_name,
                self.infrastructure.runner_zone,
                "k6 version | head -n 1",
            ),
            "machine_type": self.infrastructure.runner_machine_type(),
            "target_url": request.target_url,
            "corpus_chunk_count": metadata.get("corpus_chunk_count", "not-set"),
            "load_profile": metadata.get("load_profile", "not-set"),
            "query_set_hash": metadata.get("query_set_hash", "not-set"),
            "fixture_hash": metadata.get("fixture_hash", "not-set"),
            "truncation_fixture_hash": metadata.get(
                "truncation_fixture_hash", "not-set"
            ),
            "fixture_manifest_hash": metadata.get("fixture_manifest_hash", "not-set"),
            "corpus_manifest_hash": metadata.get("corpus_manifest_hash", "not-set"),
            "input_profile": metadata.get("input_profile", "not-set"),
            "retry_profile": metadata.get("retry_profile", "not-set"),
            "stress_preset": metadata.get("stress_preset", "not-set"),
            "target_config": request.target_config,
            "load_config": {
                "model_version": environment.get("MODEL_VERSION", "not-set"),
                "rate": environment.get("LT_RATE", "not-set"),
                "time_unit": environment.get("LT_TIME_UNIT", "not-set"),
                "duration": environment.get("LT_DURATION", "not-set"),
                "client_timeout_seconds": environment.get(
                    "LT_CLIENT_TIMEOUT_SECONDS", "not-set"
                ),
                "pre_allocated_vus": environment.get(
                    "LT_PRE_ALLOCATED_VUS", "not-set"
                ),
                "max_vus": environment.get("LT_MAX_VUS", "not-set"),
                "trace_id_namespace": environment.get(
                    "TRACE_ID_NAMESPACE", "not-set"
                ),
                "vus": environment.get("LT_VUS", "not-set"),
                "batch_size": environment.get("BATCH_SIZE", "not-set"),
                "input_set": environment.get("INPUT_SET", "not-set"),
                "input_bucket": environment.get("INPUT_BUCKET", "not-set"),
                "content_profile": environment.get("CONTENT_PROFILE", "not-set"),
                "verify_response": environment.get("VERIFY_RESPONSE", "not-set"),
                "response_verification": environment.get(
                    "RESPONSE_VERIFICATION", "not-set"
                ),
                "retry_profile": environment.get("RETRY_PROFILE", "not-set"),
                "retry_seed": environment.get("RETRY_SEED", "not-set"),
                "graceful_stop": environment.get("LT_GRACEFUL_STOP", "not-set"),
            },
        }

    def _execute_remote(
        self,
        request: ScenarioRequest,
        sync_state: dict[str, Any],
        run_id: str,
        scenario_slug: str,
    ) -> int:
        environment = request.load_environment
        arguments = [
            str(sync_state["remote_root"]),
            request.scenario,
            run_id,
            scenario_slug,
            request.target_url,
            request.iam_audience,
            request.expected_status,
            str(self.settings.runner_network_capacity_bps),
            environment.get("MODEL_VERSION", ""),
            environment.get("LT_RATE", ""),
            environment.get("LT_TIME_UNIT", ""),
            environment.get("LT_DURATION", ""),
            environment.get("LT_CLIENT_TIMEOUT_SECONDS", ""),
            environment.get("LT_PRE_ALLOCATED_VUS", ""),
            environment.get("LT_MAX_VUS", ""),
            environment.get("TRACE_ID_NAMESPACE", ""),
            environment.get("LT_VUS", ""),
            environment.get("BATCH_SIZE", ""),
            environment.get("INPUT_SET", ""),
            environment.get("INPUT_BUCKET", ""),
            environment.get("CONTENT_PROFILE", ""),
            environment.get("VERIFY_RESPONSE", ""),
            environment.get("RESPONSE_VERIFICATION", ""),
            environment.get("RETRY_PROFILE", ""),
            environment.get("RETRY_SEED", ""),
            environment.get("LT_GRACEFUL_STOP", ""),
        ]
        quoted_arguments = " ".join(shlex.quote(argument) for argument in arguments)
        command = f'bash "$HOME/{self.REMOTE_EXECUTOR}" {quoted_arguments}'
        return self.infrastructure.ssh(
            self.infrastructure.runner_name,
            self.infrastructure.runner_zone,
            command,
            check=False,
        ).returncode

    def _stop_runner_after_run(self) -> None:
        try:
            if self.infrastructure.runner_status() != "TERMINATED":
                self.infrastructure.stop_runner()
        except LoadTestError as error:
            print(f"Runner cleanup failed: {error}")

    @staticmethod
    def _environment_json(name: str, default: dict[str, Any]) -> dict[str, Any]:
        raw_value = os.environ.get(name)
        if not raw_value:
            return default
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise LoadTestError(f"{name} must contain valid JSON.") from error
        if not isinstance(value, dict):
            raise LoadTestError(f"{name} must contain a JSON object.")
        return value
