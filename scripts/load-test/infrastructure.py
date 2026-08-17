from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class LoadTestError(RuntimeError):
    """Expected operational or configuration failure."""


class CommandError(LoadTestError):
    def __init__(self, command: list[str], returncode: int, stderr: str = "") -> None:
        detail = f": {stderr.strip()}" if stderr.strip() else ""
        super().__init__(
            f"Command failed with status {returncode}: {' '.join(command)}{detail}"
        )
        self.returncode = returncode


SMOKE_ARTIFACT_TYPE = "smoke"
SEARCH_EMBEDDING_ARTIFACT_TYPE = "search-embedding"
BATCH_EMBEDDING_ARTIFACT_TYPE = "batch-embedding"
VIDEO_PIPELINE_ARTIFACT_TYPE = "video-pipeline"


def artifact_type_for_scenario(scenario: str) -> str:
    if scenario.startswith("batch-embedding-"):
        return BATCH_EMBEDDING_ARTIFACT_TYPE
    return scenario


class CommandRunner:
    """Runs local tools without shell interpolation."""

    def require(self, *commands: str) -> None:
        missing = [command for command in commands if shutil.which(command) is None]
        if missing:
            raise LoadTestError(f"Required command not found: {', '.join(missing)}")

    def run(
        self,
        command: Sequence[str],
        *,
        check: bool = True,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            list(command), check=False, capture_output=capture_output, text=True
        )
        if check and completed.returncode != 0:
            raise CommandError(list(command), completed.returncode, completed.stderr or "")
        return completed

    def output(self, command: Sequence[str]) -> str:
        return self.run(command, capture_output=True).stdout.strip()


def _non_negative_integer(name: str, default: int) -> int:
    raw_value = os.environ.get(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise LoadTestError(f"{name} must be a non-negative integer.") from error
    if value < 0:
        raise LoadTestError(f"{name} must be a non-negative integer.")
    return value


@dataclass(frozen=True)
class Settings:
    script_dir: Path
    repo_root: Path
    terraform_dir: Path
    load_test_root: Path
    artifact_root: Path
    runner_network_capacity_bps: int
    target_network_capacity_bps: int

    @classmethod
    def from_environment(cls) -> "Settings":
        script_dir = Path(__file__).resolve().parent
        repo_root = script_dir.parents[1]
        terraform_dir = Path(
            os.environ.get(
                "TF_DIR", repo_root / "infra/terraform/envs/gcp-perf"
            )
        ).resolve()
        artifact_root = Path(
            os.environ.get("ARTIFACT_ROOT", repo_root / "artifacts/load-tests")
        ).resolve()
        return cls(
            script_dir=script_dir,
            repo_root=repo_root,
            terraform_dir=terraform_dir,
            load_test_root=repo_root / "load-tests/k6",
            artifact_root=artifact_root,
            runner_network_capacity_bps=_non_negative_integer(
                "RUNNER_NETWORK_CAPACITY_BPS", 500_000_000
            ),
            target_network_capacity_bps=_non_negative_integer(
                "TARGET_NETWORK_CAPACITY_BPS", 0
            ),
        )

    def artifact_run_directory(self, test_type: str, run_id: str) -> Path:
        return self.artifact_root / test_type / run_id

    @property
    def sync_state_file(self) -> Path:
        return self.artifact_root / ".sync-state.json"

    @property
    def run_state_file(self) -> Path:
        return self.artifact_root / ".last-run.json"

    @property
    def search_session_state_file(self) -> Path:
        return self.artifact_root / ".search-embedding-session.json"

    @property
    def batch_session_state_file(self) -> Path:
        return self.artifact_root / ".batch-embedding-session.json"

    @property
    def target_vm_sampler(self) -> Path:
        return self.script_dir / "target-vm-sampler.sh"

    @property
    def remote_k6_executor(self) -> Path:
        return self.script_dir / "remote" / "k6-executor.sh"

    @property
    def remote_target_evidence(self) -> Path:
        return self.script_dir / "remote" / "target-evidence.sh"


class JsonState:
    """Reads and atomically updates one JSON state file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> dict[str, Any]:
        if not self.exists():
            raise LoadTestError(f"State file does not exist: {self.path}")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LoadTestError(f"Invalid state file: {self.path}") from error
        if not isinstance(value, dict):
            raise LoadTestError(f"State file must contain a JSON object: {self.path}")
        return value

    def write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary_path.replace(self.path)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


class Infrastructure:
    """Terraform outputs, GCE discovery, SSH, and instance lifecycle."""

    def __init__(self, settings: Settings, commands: CommandRunner) -> None:
        self.settings = settings
        self.commands = commands
        self.project_id = ""

    def prepare(self) -> None:
        self.commands.require("gcloud", "terraform")
        self.project_id = os.environ.get("PROJECT_ID") or self.commands.output(
            ["gcloud", "config", "get-value", "project"]
        )
        if not self.project_id or self.project_id == "(unset)":
            raise LoadTestError("PROJECT_ID or an active gcloud project is required.")

    def terraform_output(self, name: str) -> str:
        return self.commands.output(
            [
                "terraform",
                f"-chdir={self.settings.terraform_dir}",
                "output",
                "-raw",
                name,
            ]
        )

    def compute(
        self, *arguments: str, capture_output: bool = False, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if not self.project_id:
            raise LoadTestError("Infrastructure.prepare() must run before gcloud commands.")
        return self.commands.run(
            [
                "gcloud",
                "compute",
                *arguments,
                "--project",
                self.project_id,
                "--quiet",
            ],
            capture_output=capture_output,
            check=check,
        )

    def compute_output(self, *arguments: str) -> str:
        return self.compute(*arguments, capture_output=True).stdout.strip()

    def ssh(
        self,
        name: str,
        zone: str,
        remote_command: str,
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.compute(
            "ssh",
            name,
            "--zone",
            zone,
            "--tunnel-through-iap",
            "--command",
            remote_command,
            capture_output=capture_output,
            check=check,
        )

    def ssh_output(self, name: str, zone: str, remote_command: str) -> str:
        return self.ssh(
            name, zone, remote_command, capture_output=True
        ).stdout.strip()

    def scp(
        self,
        source: str,
        destination: str,
        *,
        zone: str,
        recursive: bool = False,
    ) -> None:
        arguments = ["scp"]
        if recursive:
            arguments.append("--recurse")
        arguments.extend(
            [source, destination, "--zone", zone, "--tunnel-through-iap"]
        )
        self.compute(*arguments)

    @cached_property
    def runner_name(self) -> str:
        return os.environ.get("LOAD_TEST_VM_NAME") or self.terraform_output(
            "load_test_vm_name"
        )

    @cached_property
    def runner_zone(self) -> str:
        return os.environ.get("LOAD_TEST_VM_ZONE") or self.terraform_output(
            "load_test_vm_zone"
        )

    @cached_property
    def search_target_name(self) -> str:
        configured_name = os.environ.get("SEARCH_EMBEDDING_VM_NAME")
        if configured_name:
            return configured_name
        endpoint = self.terraform_output("search_embedding_endpoint_url")
        private_ip = urlparse(endpoint).hostname
        if not private_ip:
            raise LoadTestError(f"Could not read a host from endpoint URL: {endpoint}")
        return self.instance_name_by_private_ip(private_ip)

    @cached_property
    def search_target_zone(self) -> str:
        return os.environ.get("SEARCH_EMBEDDING_VM_ZONE") or self.instance_zone_by_name(
            self.search_target_name
        )

    @cached_property
    def batch_target_name(self) -> str:
        return self.instance_name_by_private_ip(
            self.terraform_output("embedding_vm_private_ip")
        )

    @cached_property
    def batch_target_zone(self) -> str:
        return self.instance_zone_by_name(self.batch_target_name)

    def instance_name_by_private_ip(self, private_ip: str) -> str:
        names = self.compute_output(
            "instances",
            "list",
            f"--filter=networkInterfaces.networkIP={private_ip}",
            "--format=value(name)",
        ).splitlines()
        if len(names) != 1 or not names[0]:
            raise LoadTestError(f"Expected one VM with private IP {private_ip}.")
        return names[0]

    def instance_zone_by_name(self, name: str) -> str:
        zones = self.compute_output(
            "instances",
            "list",
            f"--filter=name=({name})",
            "--format=value(zone.basename())",
        ).splitlines()
        if len(zones) != 1 or not zones[0]:
            raise LoadTestError(f"Expected one zone for VM {name}.")
        return zones[0]

    def instance_status(self, name: str, zone: str) -> str:
        return self.compute_output(
            "instances",
            "describe",
            name,
            "--zone",
            zone,
            "--format=value(status)",
        )

    def runner_status(self) -> str:
        return self.instance_status(self.runner_name, self.runner_zone)

    def search_target_status(self) -> str:
        return self.instance_status(self.search_target_name, self.search_target_zone)

    def batch_target_status(self) -> str:
        return self.instance_status(self.batch_target_name, self.batch_target_zone)

    def start_instance(self, name: str, zone: str) -> None:
        status = self.instance_status(name, zone)
        if status == "TERMINATED":
            self.compute("instances", "start", name, "--zone", zone)
        elif status != "RUNNING":
            raise LoadTestError(f"Instance {name} is not startable from status {status}.")

    def stop_instance(self, name: str, zone: str) -> None:
        if self.instance_status(name, zone) != "TERMINATED":
            self.compute("instances", "stop", name, "--zone", zone)

    def restore_instance(self, name: str, zone: str, desired_status: str) -> None:
        current_status = self.instance_status(name, zone)
        if desired_status == "TERMINATED" and current_status != "TERMINATED":
            self.stop_instance(name, zone)
        elif desired_status == "RUNNING" and current_status == "TERMINATED":
            self.start_instance(name, zone)
        restored_status = self.instance_status(name, zone)
        if restored_status != desired_status:
            raise LoadTestError(
                f"Instance {name} restored to {restored_status} instead of {desired_status}."
            )

    def start_runner(self) -> None:
        self.start_instance(self.runner_name, self.runner_zone)
        readiness = "test -f /var/lib/biblio-k6/ready && k6 version"
        for _ in range(36):
            result = self.ssh(
                self.runner_name,
                self.runner_zone,
                readiness,
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                self.ssh(
                    self.runner_name,
                    self.runner_zone,
                    "k6 version; systemctl is-active google-cloud-ops-agent; "
                    "systemctl is-active k6-runner-autoshutdown.timer",
                )
                return
            time.sleep(10)
        try:
            self.stop_runner()
        except LoadTestError as cleanup_error:
            raise LoadTestError(
                "Runner startup timed out and the runner stop also failed: "
                f"{cleanup_error}"
            ) from cleanup_error
        raise LoadTestError("Runner startup did not become ready within six minutes.")

    def stop_runner(self) -> None:
        self.stop_instance(self.runner_name, self.runner_zone)

    def runner_machine_type(self) -> str:
        return self.compute_output(
            "instances",
            "describe",
            self.runner_name,
            "--zone",
            self.runner_zone,
            "--format=value(machineType.basename())",
        )

    def show_runner_status(self) -> None:
        self.compute(
            "instances",
            "describe",
            self.runner_name,
            "--zone",
            self.runner_zone,
            "--format=table(name,zone.basename(),status,machineType.basename(),"
            "networkInterfaces[0].networkIP,networkInterfaces[0].accessConfigs[0].natIP)",
        )
