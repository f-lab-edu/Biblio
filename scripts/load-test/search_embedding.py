from __future__ import annotations

import hashlib
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from infrastructure import (
    CommandRunner,
    Infrastructure,
    JsonState,
    LoadTestError,
    Settings,
)
from k6_runner import (
    ArtifactManager,
    K6Runner,
    ScenarioRequest,
    compact_utc_timestamp,
    utc_timestamp,
)
from search_target import SearchTarget, TargetMonitor


def duration_seconds(value: str) -> float:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m)", value)
    if not match:
        raise LoadTestError(f"Duration must use ms, s, or m units: {value}")
    multiplier = {"ms": 0.001, "s": 1.0, "m": 60.0}[match.group(2)]
    seconds = float(match.group(1)) * multiplier
    if seconds <= 0:
        raise LoadTestError(f"Duration must be greater than zero: {value}")
    return seconds


@dataclass(frozen=True)
class SearchRunConfig:
    rate: int
    time_unit: str = "1s"
    duration: str = "2m"
    client_timeout_seconds: int = 15
    pre_allocated_vus: int | None = None
    max_vus: int | None = None

    def validated(self) -> "SearchRunConfig":
        if self.rate <= 0:
            raise LoadTestError("A positive integer --rate is required.")
        if self.client_timeout_seconds <= 0:
            raise LoadTestError("--client-timeout must be a positive integer.")
        duration_seconds(self.duration)
        unit_seconds = duration_seconds(self.time_unit)
        required_vus = math.ceil(self.rate * self.client_timeout_seconds / unit_seconds)
        pre_allocated = (
            required_vus if self.pre_allocated_vus is None else self.pre_allocated_vus
        )
        max_vus = pre_allocated if self.max_vus is None else self.max_vus
        if pre_allocated <= 0 or max_vus <= 0:
            raise LoadTestError("VU values must be positive integers.")
        if pre_allocated < required_vus or max_vus < pre_allocated:
            raise LoadTestError(
                "VU allocation must satisfy required <= preAllocated <= max "
                f"({required_vus} <= {pre_allocated} <= {max_vus})."
            )
        return SearchRunConfig(
            rate=self.rate,
            time_unit=self.time_unit,
            duration=self.duration,
            client_timeout_seconds=self.client_timeout_seconds,
            pre_allocated_vus=pre_allocated,
            max_vus=max_vus,
        )


class SearchEmbeddingSession:
    """Coordinates session state and the order of search load-test operations."""

    def __init__(
        self,
        settings: Settings,
        commands: CommandRunner,
        infrastructure: Infrastructure,
        k6_runner: K6Runner,
        artifacts: ArtifactManager,
    ) -> None:
        self.settings = settings
        self.commands = commands
        self.infrastructure = infrastructure
        self.k6_runner = k6_runner
        self.artifacts = artifacts
        self.state = JsonState(settings.search_session_state_file)
        self.target = SearchTarget(settings, infrastructure, k6_runner)
        self.monitor = TargetMonitor(settings, infrastructure)

    def start(self, model_version: str) -> None:
        self._validate_start(model_version)
        self._write_initial_state(model_version)
        try:
            self._prepare_session(model_version)
        except Exception:
            self._stop_after_failed_start()
            raise
        print("Search embedding session is ready.")

    def run(self, requested_config: SearchRunConfig) -> Path:
        session = self._active_session()
        config = requested_config.validated()
        self._assert_running_instances()
        run_id = f"{compact_utc_timestamp()}-search-r{config.rate}"
        trace_namespace = self._trace_namespace(run_id)
        self._set_active_run(run_id)
        if not self._start_monitor_with_cleanup(run_id):
            raise LoadTestError("Target VM sampler did not start.")
        errors: list[str] = []
        recovered = False
        monitor_stopped = False
        try:
            self._run_k6(session, config, run_id, trace_namespace, errors)
            recovered = self._wait_for_recovery(trace_namespace)
            if not recovered:
                errors.append("Search target did not recover within one minute.")
            monitor_stopped = self._finish_target_collection(
                run_id, str(session["model_version"]), trace_namespace, errors
            )
            self._merge_metadata(run_id, recovered, errors)
        finally:
            if monitor_stopped:
                self._clear_active_run()
        result_dir = self.settings.artifact_root / run_id / "search-embedding"
        print(f"Search embedding run results: {result_dir}")
        if errors:
            raise LoadTestError("Search embedding run was incomplete: " + " | ".join(errors))
        return result_dir

    def stop(self) -> None:
        if not self.state.exists():
            print("No search embedding session state exists.")
            return
        session = self.state.read()
        errors: list[str] = []
        active_run_id = str(session.get("active_run_id", ""))
        if active_run_id:
            self._stop_active_monitor(active_run_id, errors)
        self._restore_initial_instances(session, errors)
        if errors:
            raise LoadTestError(
                f"Session cleanup was incomplete; state was retained at {self.state.path}: "
                + " | ".join(errors)
            )
        self.state.delete()
        print("Search embedding session stopped and initial VM states were restored.")

    def _validate_start(self, model_version: str) -> None:
        if self.state.exists():
            raise LoadTestError(
                "A search embedding session already exists. Run search-embedding-stop first."
            )
        if self.settings.batch_session_state_file.is_file():
            raise LoadTestError(
                "A batch embedding session is active. Stop it before starting a search session."
            )
        if not model_version:
            raise LoadTestError("search-embedding-start requires --model-version.")
        if not os.access(self.settings.target_vm_sampler, os.X_OK):
            raise LoadTestError(
                f"Target VM sampler is not executable: {self.settings.target_vm_sampler}"
            )

    def _prepare_session(self, model_version: str) -> None:
        if self.infrastructure.batch_target_status() == "RUNNING":
            self.infrastructure.stop_instance(
                self.infrastructure.batch_target_name,
                self.infrastructure.batch_target_zone,
            )
        self.infrastructure.start_instance(
            self.infrastructure.search_target_name,
            self.infrastructure.search_target_zone,
        )
        self.target.wait_until_ready(model_version)
        self.target.assert_no_recent_requests()
        self.infrastructure.start_runner()
        self.k6_runner.sync_sources()
        self.target.inspect_scenario()
        self.target.probe(self.state.read(), "loadtest-probe-session-start")
        self._record_ready_target()

    def _write_initial_state(self, model_version: str) -> None:
        statuses = self._initial_instance_statuses()
        endpoint = self.infrastructure.terraform_output("search_embedding_endpoint_url")
        self.commands.require("git")
        self.state.write(
            {
                "status": "starting",
                "started_at": utc_timestamp(),
                "model_version": model_version,
                "query_set_hash": self._sha256(
                    self.settings.load_test_root / "data/search-embedding-inputs.json"
                ),
                "scenario_hash": self._sha256(
                    self.settings.load_test_root / "scenarios/search-embedding.js"
                ),
                "git_status": self.commands.output(
                    ["git", "-C", str(self.settings.repo_root), "status", "--porcelain"]
                ),
                "initial_status": statuses,
                "target": {
                    "name": self.infrastructure.search_target_name,
                    "zone": self.infrastructure.search_target_zone,
                    "url": f"{endpoint.rstrip('/')}/embed",
                },
            }
        )

    def _initial_instance_statuses(self) -> dict[str, str]:
        statuses = {
            "runner": self.infrastructure.runner_status(),
            "search_target": self.infrastructure.search_target_status(),
            "batch_target": self.infrastructure.batch_target_status(),
        }
        invalid_status = next(
            (status for status in statuses.values() if status not in {"RUNNING", "TERMINATED"}),
            None,
        )
        if invalid_status:
            raise LoadTestError(
                f"Session cannot start while a VM is in transient status {invalid_status}."
            )
        return statuses

    def _record_ready_target(self) -> None:
        session = self.state.read()
        session["status"] = "active"
        session["target"].update(self.target.deployment_snapshot())
        self.state.write(session)

    def _scenario_request(
        self,
        session: dict[str, Any],
        config: SearchRunConfig,
        run_id: str,
        trace_namespace: str,
    ) -> ScenarioRequest:
        session_metadata = {
            key: session[key]
            for key in ("started_at", "scenario_hash", "git_status", "initial_status")
        }
        session_metadata.update(
            {
                "target_boot_id": session["target"]["boot_id"],
                "target_container_id": session["target"]["container_id"],
            }
        )
        return ScenarioRequest(
            scenario="scenarios/search-embedding.js",
            target_url=str(session["target"]["url"]),
            target_config={
                "deployment": session["target"]["config"],
                "session": session_metadata,
            },
            metadata={
                "query_set_hash": str(session["query_set_hash"]),
                "load_profile": f"{config.rate} RPS / {config.duration}",
            },
            load_environment={
                "MODEL_VERSION": str(session["model_version"]),
                "LT_RATE": str(config.rate),
                "LT_TIME_UNIT": config.time_unit,
                "LT_DURATION": config.duration,
                "LT_CLIENT_TIMEOUT_SECONDS": str(config.client_timeout_seconds),
                "LT_PRE_ALLOCATED_VUS": str(config.pre_allocated_vus),
                "LT_MAX_VUS": str(config.max_vus),
                "TRACE_ID_NAMESPACE": trace_namespace,
            },
            run_id=run_id,
            keep_runner_running=True,
        )

    def _run_k6(
        self,
        session: dict[str, Any],
        config: SearchRunConfig,
        run_id: str,
        trace_namespace: str,
        errors: list[str],
    ) -> None:
        self._record_error(
            lambda: self.k6_runner.run_scenario(
                self._scenario_request(session, config, run_id, trace_namespace)
            ),
            errors,
        )

    def _wait_for_recovery(self, trace_namespace: str) -> bool:
        session = self.state.read()
        for _ in range(12):
            if self.target.is_ready(str(session["model_version"])):
                try:
                    self.target.probe(session, f"{trace_namespace}-ffffffffffff")
                    return True
                except LoadTestError:
                    pass
            time.sleep(5)
        return False

    def _start_monitor_with_cleanup(self, run_id: str) -> bool:
        try:
            self.monitor.start(run_id)
            return True
        except LoadTestError:
            try:
                self.monitor.stop(run_id)
                self._clear_active_run()
            except LoadTestError:
                print("Sampler cleanup could not be confirmed; active run state was retained.")
            return False

    def _finish_target_collection(
        self,
        run_id: str,
        model_version: str,
        trace_namespace: str,
        errors: list[str],
    ) -> bool:
        monitor_stopped = self._operation_succeeded(
            lambda: self.monitor.stop(run_id), errors
        )
        for operation in (
            lambda: self.monitor.collect_evidence(
                run_id, model_version, trace_namespace
            ),
            lambda: self.artifacts.collect_target_results(run_id),
        ):
            self._record_error(operation, errors)
        return monitor_stopped

    def _stop_active_monitor(self, run_id: str, errors: list[str]) -> None:
        try:
            target_status = self.infrastructure.search_target_status()
        except LoadTestError as error:
            errors.append(str(error))
            self._record_error(lambda: self.monitor.stop(run_id), errors)
            return
        if target_status == "RUNNING":
            self._record_error(lambda: self.monitor.stop(run_id), errors)

    def _merge_metadata(self, run_id: str, recovered: bool, errors: list[str]) -> None:
        self._record_error(
            lambda: self.artifacts.merge_search_metadata(run_id, recovered), errors
        )

    def _restore_initial_instances(
        self, session: dict[str, Any], errors: list[str]
    ) -> None:
        initial_status = session["initial_status"]
        instances = (
            (
                self.infrastructure.search_target_name,
                self.infrastructure.search_target_zone,
                initial_status["search_target"],
            ),
            (
                self.infrastructure.batch_target_name,
                self.infrastructure.batch_target_zone,
                initial_status["batch_target"],
            ),
            (
                self.infrastructure.runner_name,
                self.infrastructure.runner_zone,
                initial_status["runner"],
            ),
        )
        for name, zone, desired_status in instances:
            self._record_error(
                lambda n=name, z=zone, s=desired_status: self.infrastructure.restore_instance(
                    n, z, s
                ),
                errors,
            )

    def _assert_running_instances(self) -> None:
        if (
            self.infrastructure.runner_status() != "RUNNING"
            or self.infrastructure.search_target_status() != "RUNNING"
        ):
            raise LoadTestError("Both runner and search target must be RUNNING.")

    def _active_session(self) -> dict[str, Any]:
        session = self.state.read()
        if session.get("status") != "active":
            raise LoadTestError("Run search-embedding-start before search-embedding-run.")
        return session

    def _set_active_run(self, run_id: str) -> None:
        session = self.state.read()
        session["active_run_id"] = run_id
        self.state.write(session)

    def _clear_active_run(self) -> None:
        if self.state.exists():
            session = self.state.read()
            session.pop("active_run_id", None)
            self.state.write(session)

    def _stop_after_failed_start(self) -> None:
        try:
            self.stop()
        except LoadTestError as error:
            print(f"Failed start cleanup was incomplete: {error}")

    @staticmethod
    def _record_error(operation: Callable[[], object], errors: list[str]) -> None:
        try:
            operation()
        except LoadTestError as error:
            errors.append(str(error))

    @staticmethod
    def _operation_succeeded(
        operation: Callable[[], object], errors: list[str]
    ) -> bool:
        try:
            operation()
        except LoadTestError as error:
            errors.append(str(error))
            return False
        return True

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _trace_namespace(run_id: str) -> str:
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return f"{digest[:8]}-{digest[8:12]}-4{digest[12:15]}-8{digest[15:18]}"
