from __future__ import annotations

import hashlib
import json
import os
import shutil
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
from search_embedding import duration_seconds
from search_target import BatchTarget, TargetMonitor

CAPACITY_SCENARIO = "scenarios/batch-embedding-capacity.js"
CONTENT_PROFILES = {
    "all",
    "narration",
    "mixed_terms",
    "ocr_numeric",
    "scene_tags",
    "fallback_text",
}
INPUT_BUCKETS = {
    "capacity": {"balanced", "short", "medium", "long", "xlong", "boundary"},
    "truncation": {"balanced", "over_limit", "observed_tail"},
    "observed-mix": {"observed-mix"},
}
RETRY_PROFILES = {"raw", "worker-client"}
RESPONSE_VERIFICATION_PROFILES = {"none", "sampled", "all"}
STRESS_PRESETS = {
    "S1": {"vus": 1, "duration": "2m", "retry_profile": "raw"},
    "S2": {"vus": 4, "duration": "10m", "retry_profile": "raw"},
    "S3": {"vus": 4, "duration": "30m", "retry_profile": "worker-client"},
    "S4": {"vus": 5, "duration": "5m", "retry_profile": "worker-client"},
}


@dataclass(frozen=True)
class BatchRunConfig:
    scenario: str
    input_set: str = "capacity"
    input_bucket: str = "balanced"
    content_profile: str = "all"
    batch_size: int = 4
    client_timeout_seconds: int = 180
    response_verification: str = "none"
    retry_profile: str = "raw"
    retry_seed: int = 104
    graceful_stop: str = "30s"
    vus: int = 1
    duration: str = "2m"
    stress_preset: str | None = None

    @classmethod
    def stress(cls, preset: str) -> "BatchRunConfig":
        normalized = preset.upper()
        try:
            values = STRESS_PRESETS[normalized]
        except KeyError as error:
            raise LoadTestError("Stress preset must be S1, S2, S3, or S4.") from error
        return cls(
            scenario="capacity",
            input_set="observed-mix",
            input_bucket="observed-mix",
            content_profile="all",
            batch_size=4,
            client_timeout_seconds=180,
            response_verification="sampled",
            retry_seed=104,
            graceful_stop="4m",
            stress_preset=normalized,
            **values,
        ).validated()

    def validated(self) -> "BatchRunConfig":
        if self.scenario != "capacity":
            raise LoadTestError("Batch scenario must be capacity.")
        if self.input_set not in INPUT_BUCKETS:
            raise LoadTestError("Input set must be capacity or truncation.")
        if self.input_bucket not in INPUT_BUCKETS[self.input_set]:
            raise LoadTestError(
                f"Input bucket {self.input_bucket} is invalid for {self.input_set}."
            )
        if self.content_profile not in CONTENT_PROFILES:
            raise LoadTestError(f"Unknown content profile: {self.content_profile}.")
        if self.input_set == "observed-mix" and self.content_profile != "all":
            raise LoadTestError("Observed mix requires content profile all.")
        if self.stress_preset is not None and self.stress_preset not in STRESS_PRESETS:
            raise LoadTestError("Stress preset must be S1, S2, S3, or S4.")
        if self.retry_profile not in RETRY_PROFILES:
            raise LoadTestError("Retry profile must be raw or worker-client.")
        if self.response_verification not in RESPONSE_VERIFICATION_PROFILES:
            raise LoadTestError(
                "Response verification must be none, sampled, or all."
            )
        positive_values = {
            "batch size": self.batch_size,
            "client timeout": self.client_timeout_seconds,
            "VUs": self.vus,
        }
        invalid_name = next(
            (name for name, value in positive_values.items() if value <= 0), None
        )
        if invalid_name:
            raise LoadTestError(f"Batch {invalid_name} must be a positive integer.")
        if self.batch_size > 32:
            raise LoadTestError("Batch size cannot exceed the live request limit of 32.")
        if self.retry_seed < 0:
            raise LoadTestError("Batch retry seed must be a non-negative integer.")
        duration_seconds(self.duration)
        duration_seconds(self.graceful_stop)
        return self

    @property
    def scenario_path(self) -> str:
        return CAPACITY_SCENARIO

    @property
    def scenario_slug(self) -> str:
        return Path(self.scenario_path).stem


class BatchEmbeddingSession:
    """Coordinates isolated batch embedding runs and restores initial VM states."""

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
        self.state = JsonState(settings.batch_session_state_file)
        self.target = BatchTarget(settings, infrastructure, k6_runner)
        self.monitor = TargetMonitor(
            settings,
            infrastructure,
            target_name=infrastructure.batch_target_name,
            target_zone=infrastructure.batch_target_zone,
        )

    def start(self, model_version: str) -> None:
        self._validate_start(model_version)
        self._write_initial_state(model_version)
        try:
            self._prepare_session(model_version)
        except Exception:
            self._stop_after_failed_start()
            raise
        print("Batch embedding session is ready.")

    def run(self, requested_config: BatchRunConfig) -> Path:
        session = self._active_session()
        config = requested_config.validated()
        self._assert_running_instances()
        if config.stress_preset:
            self._assert_stress_worker_limits(config)
        self._assert_no_active_run(session)
        run_id = self._run_id(config)
        trace_namespace = self._trace_namespace(run_id)
        self._set_active_run(run_id)
        if not self._start_monitor_with_cleanup(run_id):
            raise LoadTestError("Batch target VM sampler did not start.")
        errors: list[str] = []
        recovered = False
        monitor_stopped = False
        try:
            self._record_error(
                lambda: self.k6_runner.run_scenario(
                    self._scenario_request(
                        session, config, run_id, trace_namespace
                    )
                ),
                errors,
            )
            self._record_error(
                lambda: self._copy_fixture_manifest(
                    run_id, config.scenario_slug
                ),
                errors,
            )
            recovered = self._wait_for_recovery(trace_namespace)
            if not recovered:
                errors.append("Batch target did not recover within one minute.")
            monitor_stopped = self._finish_target_collection(
                run_id,
                config.scenario_slug,
                str(session["model_version"]),
                trace_namespace,
                errors,
            )
            self._record_error(
                lambda: self.artifacts.merge_embedding_metadata(
                    run_id,
                    config.scenario_slug,
                    recovered,
                    acceptance_key="batch_acceptance",
                ),
                errors,
            )
        finally:
            if monitor_stopped:
                self._clear_active_run()
        result_dir = (
            self.settings.artifact_root / run_id / config.scenario_slug
        )
        print(f"Batch embedding run results: {result_dir}")
        if errors:
            raise LoadTestError("Batch embedding run was incomplete: " + " | ".join(errors))
        return result_dir

    def stop(self) -> None:
        if not self.state.exists():
            print("No batch embedding session state exists.")
            return
        session = self.state.read()
        errors: list[str] = []
        active_run_id = str(session.get("active_run_id", ""))
        if active_run_id and self.infrastructure.batch_target_status() == "RUNNING":
            self._record_error(lambda: self.monitor.stop(active_run_id), errors)
        self._restore_initial_instances(session, errors)
        if errors:
            raise LoadTestError(
                f"Session cleanup was incomplete; state was retained at {self.state.path}: "
                + " | ".join(errors)
            )
        self.state.delete()
        print("Batch embedding session stopped and initial VM states were restored.")

    def _validate_start(self, model_version: str) -> None:
        if self.state.exists():
            raise LoadTestError(
                "A batch embedding session already exists. Run batch-embedding-stop first."
            )
        if self.settings.search_session_state_file.is_file():
            raise LoadTestError(
                "A search embedding session is active. Stop it before starting a batch session."
            )
        if not model_version:
            raise LoadTestError("batch-embedding-start requires --model-version.")
        if not os.access(self.settings.target_vm_sampler, os.X_OK):
            raise LoadTestError(
                f"Target VM sampler is not executable: {self.settings.target_vm_sampler}"
            )

    def _write_initial_state(self, model_version: str) -> None:
        statuses = self._initial_instance_statuses()
        endpoint = self.infrastructure.terraform_output("batch_embedding_endpoint_url")
        manifest_path = (
            self.settings.load_test_root
            / "data/batch-embedding-enriched-texts.manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        fixture_hashes = self._validated_fixture_hashes(manifest, model_version)
        self.commands.require("git")
        self.state.write(
            {
                "status": "starting",
                "started_at": utc_timestamp(),
                "model_version": model_version,
                "fixture_hashes": fixture_hashes,
                "fixture_manifest_hash": self._sha256(manifest_path),
                "scenario_hashes": {
                    "capacity": self._sha256(
                        self.settings.load_test_root / CAPACITY_SCENARIO
                    ),
                },
                "git_status": self.commands.output(
                    ["git", "-C", str(self.settings.repo_root), "status", "--porcelain"]
                ),
                "initial_status": statuses,
                "worker": self._worker_deployment_snapshot(),
                "target": {
                    "name": self.infrastructure.batch_target_name,
                    "zone": self.infrastructure.batch_target_zone,
                    "url": f"{endpoint.rstrip('/')}/embed",
                },
            }
        )

    def _prepare_session(self, model_version: str) -> None:
        if self.infrastructure.search_target_status() == "RUNNING":
            self.infrastructure.stop_instance(
                self.infrastructure.search_target_name,
                self.infrastructure.search_target_zone,
            )
        self.infrastructure.start_instance(
            self.infrastructure.batch_target_name,
            self.infrastructure.batch_target_zone,
        )
        self.target.wait_until_ready(model_version)
        self.target.assert_no_recent_requests()
        self.infrastructure.start_runner()
        self.k6_runner.sync_sources()
        self.target.inspect_scenarios()
        self.target.probe(self.state.read(), "loadtest-probe-batch-session-start")
        session = self.state.read()
        session["status"] = "active"
        session["target"].update(self.target.deployment_snapshot())
        self.state.write(session)

    def _scenario_request(
        self,
        session: dict[str, Any],
        config: BatchRunConfig,
        run_id: str,
        trace_namespace: str,
    ) -> ScenarioRequest:
        hashes = session["fixture_hashes"]
        return ScenarioRequest(
            scenario=config.scenario_path,
            target_url=str(session["target"]["url"]),
            target_config=self._target_config(session, config),
            metadata={
                "fixture_hash": str(hashes["fixture_sha256"]),
                "truncation_fixture_hash": str(
                    hashes["truncation_fixture_sha256"]
                ),
                "fixture_manifest_hash": str(session["fixture_manifest_hash"]),
                "input_profile": (
                    f"set={config.input_set},bucket={config.input_bucket},"
                    f"content={config.content_profile}"
                ),
                "load_profile": self._load_profile(config),
                "retry_profile": config.retry_profile,
                "stress_preset": config.stress_preset or "not-set",
            },
            load_environment=self._load_environment(
                session, config, trace_namespace
            ),
            run_id=run_id,
            keep_runner_running=True,
        )

    @staticmethod
    def _target_config(
        session: dict[str, Any], config: BatchRunConfig
    ) -> dict[str, Any]:
        return {
            "deployment": session["target"]["config"],
            "session": {
                "started_at": session["started_at"],
                "initial_status": session["initial_status"],
                "target_boot_id": session["target"]["boot_id"],
                "target_container_id": session["target"]["container_id"],
                "scenario_hash": session["scenario_hashes"]["capacity"],
                "git_status": session["git_status"],
                "worker": session.get("worker", {}),
            },
        }

    @staticmethod
    def _load_environment(
        session: dict[str, Any],
        config: BatchRunConfig,
        trace_namespace: str,
    ) -> dict[str, str]:
        return {
            "MODEL_VERSION": str(session["model_version"]),
            "LT_DURATION": config.duration,
            "LT_CLIENT_TIMEOUT_SECONDS": str(config.client_timeout_seconds),
            "TRACE_ID_NAMESPACE": trace_namespace,
            "LT_VUS": str(config.vus),
            "BATCH_SIZE": str(config.batch_size),
            "INPUT_SET": config.input_set,
            "INPUT_BUCKET": config.input_bucket,
            "CONTENT_PROFILE": config.content_profile,
            "RESPONSE_VERIFICATION": config.response_verification,
            "RETRY_PROFILE": config.retry_profile,
            "RETRY_SEED": str(config.retry_seed),
            "LT_GRACEFUL_STOP": config.graceful_stop,
        }

    def _finish_target_collection(
        self,
        run_id: str,
        scenario_slug: str,
        model_version: str,
        trace_namespace: str,
        errors: list[str],
    ) -> bool:
        monitor_stopped = self._operation_succeeded(
            lambda: self.monitor.stop(run_id), errors
        )
        self._record_error(
            lambda: self.monitor.collect_evidence(
                run_id, model_version, trace_namespace
            ),
            errors,
        )
        self._record_error(
            lambda: self.artifacts.collect_target_results(
                run_id,
                scenario_slug,
                target_name=self.infrastructure.batch_target_name,
                target_zone=self.infrastructure.batch_target_zone,
            ),
            errors,
        )
        return monitor_stopped

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

    def _copy_fixture_manifest(self, run_id: str, scenario_slug: str) -> None:
        source = (
            self.settings.load_test_root
            / "data/batch-embedding-enriched-texts.manifest.json"
        )
        destination = (
            self.settings.artifact_root
            / run_id
            / scenario_slug
            / "fixture-manifest.json"
        )
        try:
            shutil.copyfile(source, destination)
        except OSError as error:
            raise LoadTestError(
                f"Could not copy fixture manifest to {destination}."
            ) from error

    def _validated_fixture_hashes(
        self, manifest: dict[str, Any], model_version: str
    ) -> dict[str, str]:
        if manifest.get("target_model_version") != model_version:
            raise LoadTestError(
                "Fixture manifest model version does not match the requested model."
            )
        hashes = manifest.get("hashes")
        if not isinstance(hashes, dict):
            raise LoadTestError("Fixture manifest hashes are missing.")
        data_dir = self.settings.load_test_root / "data"
        files = {
            "fixture_sha256": data_dir / "batch-embedding-enriched-texts.json",
            "truncation_fixture_sha256": data_dir
            / "batch-embedding-truncation-inputs.json",
            "boundary_fixture_sha256": data_dir
            / "batch-embedding-boundary-inputs.json",
            "db_profile_sha256": data_dir / "batch-embedding-db-profile.json",
        }
        for hash_name, path in files.items():
            if hashes.get(hash_name) != self._sha256(path):
                raise LoadTestError(f"Fixture hash mismatch: {path.name}.")
        self._validate_observed_mix(
            json.loads(files["db_profile_sha256"].read_text(encoding="utf-8")),
            manifest,
        )
        return {name: str(hashes[name]) for name in files}

    @staticmethod
    def _validate_observed_mix(
        db_profile: dict[str, Any], manifest: dict[str, Any]
    ) -> None:
        observed_mix = db_profile.get("observed_mix")
        counts = observed_mix.get("raw_token_bucket_counts") if isinstance(
            observed_mix, dict
        ) else None
        expected_buckets = {
            "short",
            "medium",
            "long",
            "xlong",
            "boundary",
            "over_limit",
            "observed_tail",
        }
        if not isinstance(counts, dict) or set(counts) != expected_buckets:
            raise LoadTestError("DB profile observed_mix buckets are incomplete.")
        if any(not isinstance(value, int) or value < 0 for value in counts.values()):
            raise LoadTestError("DB profile observed_mix counts must be non-negative integers.")
        sample_count = observed_mix.get("sample_count")
        if not isinstance(sample_count, int) or sum(counts.values()) != sample_count:
            raise LoadTestError("DB profile observed_mix counts do not match sample_count.")
        manifest_mix = manifest.get("db_profile", {}).get("observed_mix")
        comparable = {
            "source": observed_mix.get("source"),
            "sample_count": sample_count,
            "effective_token_limit": observed_mix.get("effective_token_limit"),
            "raw_token_bucket_counts": counts,
            "raw_text_persisted": observed_mix.get("raw_text_persisted"),
        }
        if manifest_mix != comparable:
            raise LoadTestError("Fixture manifest observed_mix does not match DB profile.")

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

    def _restore_initial_instances(
        self, session: dict[str, Any], errors: list[str]
    ) -> None:
        initial = session["initial_status"]
        instances = (
            (
                self.infrastructure.search_target_name,
                self.infrastructure.search_target_zone,
                initial["search_target"],
            ),
            (
                self.infrastructure.batch_target_name,
                self.infrastructure.batch_target_zone,
                initial["batch_target"],
            ),
            (
                self.infrastructure.runner_name,
                self.infrastructure.runner_zone,
                initial["runner"],
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
            or self.infrastructure.batch_target_status() != "RUNNING"
        ):
            raise LoadTestError("Both runner and batch target must be RUNNING.")

    def _worker_deployment_snapshot(self) -> dict[str, Any]:
        region = self.infrastructure.batch_target_zone.rsplit("-", 1)[0]
        raw_snapshot = self.commands.output(
            [
                "gcloud",
                "run",
                "services",
                "describe",
                "pipeline-worker",
                "--project",
                self.infrastructure.project_id,
                "--region",
                region,
                "--format=json",
            ]
        )
        try:
            payload = json.loads(raw_snapshot)
            template = payload["spec"]["template"]
            annotations = template["metadata"]["annotations"]
            container = template["spec"]["containers"][0]
            environment = {
                item["name"]: item.get("value", "")
                for item in container.get("env", [])
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            return {
                "revision": payload["status"]["latestReadyRevisionName"],
                "max_instance_count": int(
                    annotations["autoscaling.knative.dev/maxScale"]
                ),
                "worker_concurrency": int(environment["WORKER_CONCURRENCY"]),
                "embedding_batch_size": int(environment["EMBEDDING_BATCH_SIZE"]),
                "embedding_timeout_sec": int(environment["EMBEDDING_TIMEOUT_SEC"]),
            }
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise LoadTestError(
                "Could not read the live pipeline-worker scaling and embedding settings."
            ) from error

    def _assert_stress_worker_limits(self, config: BatchRunConfig) -> None:
        worker = self._worker_deployment_snapshot()
        expected = {
            "max_instance_count": 1,
            "worker_concurrency": 4,
            "embedding_batch_size": 4,
            "embedding_timeout_sec": 180,
        }
        mismatches = [
            f"{name}={worker.get(name)} (expected {value})"
            for name, value in expected.items()
            if worker.get(name) != value
        ]
        if mismatches:
            raise LoadTestError(
                "Stress presets no longer match the live Worker: " + ", ".join(mismatches)
            )
        expected_vus = {
            "S1": 1,
            "S2": 4,
            "S3": 4,
            "S4": 5,
        }[str(config.stress_preset)]
        if config.vus != expected_vus or config.batch_size != 4:
            raise LoadTestError("Stress preset VUs or batch size were changed unexpectedly.")

    def _active_session(self) -> dict[str, Any]:
        session = self.state.read()
        if session.get("status") != "active":
            raise LoadTestError("Run batch-embedding-start before a batch run.")
        return session

    @staticmethod
    def _assert_no_active_run(session: dict[str, Any]) -> None:
        if session.get("active_run_id"):
            raise LoadTestError(
                "A batch embedding run is already active. Stop the session before retrying."
            )

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
    def _load_profile(config: BatchRunConfig) -> str:
        return f"{config.vus} VU / batch {config.batch_size} / {config.duration}"

    @staticmethod
    def _run_id(config: BatchRunConfig) -> str:
        suffix = f"capacity-b{config.batch_size}-v{config.vus}"
        return f"{compact_utc_timestamp()}-batch-{suffix}"

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
