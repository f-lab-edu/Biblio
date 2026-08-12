from __future__ import annotations

import shlex
import time
from typing import Any

from infrastructure import Infrastructure, LoadTestError, Settings
from k6_runner import K6Runner


class EmbeddingTarget:
    """Checks shared endpoint readiness, isolation, and deployment identity."""

    COMPOSE_DIR = "/opt/biblio/managed-embedding-endpoint"

    def __init__(
        self,
        settings: Settings,
        infrastructure: Infrastructure,
        k6_runner: K6Runner,
        *,
        target_name: str,
        target_zone: str,
        config_keys: tuple[str, ...],
    ) -> None:
        self.settings = settings
        self.infrastructure = infrastructure
        self.k6_runner = k6_runner
        self.target_name = target_name
        self.target_zone = target_zone
        self.config_keys = config_keys

    def wait_until_ready(self, model_version: str) -> None:
        for _ in range(72):
            if self.is_ready(model_version):
                return
            time.sleep(10)
        raise LoadTestError(
            "Embedding target did not become ready within twelve minutes."
        )

    def is_ready(self, model_version: str) -> bool:
        command = (
            "systemctl is-active biblio-managed-embedding-endpoint.service >/dev/null && "
            "curl -fsS http://127.0.0.1:8000/health | "
            f"jq -e --arg model {shlex.quote(model_version)} "
            "'(.status == \"ok\") and (.ready_model_versions | index($model) != null)' "
            ">/dev/null"
        )
        completed = self.infrastructure.ssh(
            self.target_name,
            self.target_zone,
            command,
            capture_output=True,
            check=False,
        )
        return completed.returncode == 0

    def assert_no_recent_requests(self) -> None:
        pipeline = (
            f"sudo -n docker compose --project-directory {self.COMPOSE_DIR} "
            f"-f {self.COMPOSE_DIR}/docker-compose.yml logs --no-color --no-log-prefix "
            "--since 1m managed-embedding-endpoint | "
            "jq -Rrc 'fromjson? | select(.msg == \"embedding.admission\")' | wc -l"
        )
        command = f"bash -c {shlex.quote(f'set -euo pipefail; {pipeline}')}"
        output = self.infrastructure.ssh_output(
            self.target_name,
            self.target_zone,
            command,
        )
        try:
            count = int(output)
        except ValueError as error:
            raise LoadTestError("Could not count recent endpoint requests.") from error
        if count:
            raise LoadTestError(
                f"Recent endpoint traffic was found ({count} admission records in one minute)."
            )

    def inspect_scenario(self, scenario: str) -> None:
        remote_root = str(self.k6_runner.sync_state.read()["remote_root"])
        command = (
            f'cd "$HOME"/{shlex.quote(remote_root)} && '
            f"k6 inspect {shlex.quote(scenario)} >/dev/null"
        )
        self.infrastructure.ssh(
            self.infrastructure.runner_name,
            self.infrastructure.runner_zone,
            command,
        )

    def deployment_snapshot(self) -> dict[str, Any]:
        return {
            "boot_id": self.infrastructure.ssh_output(
                self.target_name,
                self.target_zone,
                "cat /proc/sys/kernel/random/boot_id",
            ),
            "container_id": self.infrastructure.ssh_output(
                self.target_name,
                self.target_zone,
                f"sudo -n docker compose --project-directory {self.COMPOSE_DIR} "
                f"-f {self.COMPOSE_DIR}/docker-compose.yml ps -q managed-embedding-endpoint",
            ),
            "config": self._deployment_config(),
        }

    def _deployment_config(self) -> dict[str, str]:
        conditions = " || ".join(f'$1 == "{key}"' for key in self.config_keys)
        command = f"sudo -n awk -F= '{conditions} {{print}}' {self.COMPOSE_DIR}/.env"
        env_lines = self.infrastructure.ssh_output(
            self.target_name,
            self.target_zone,
            command,
        )
        config = dict(line.split("=", 1) for line in env_lines.splitlines() if "=" in line)
        config["machine_type"] = self.infrastructure.compute_output(
            "instances",
            "describe",
            self.target_name,
            "--zone",
            self.target_zone,
            "--format=value(machineType.basename())",
        )
        return config


class TargetMonitor:
    """Starts the target VM sampler and triggers endpoint evidence collection."""

    REMOTE_SAMPLER = "biblio-target-vm-sampler.sh"
    REMOTE_EVIDENCE = "biblio-target-evidence.sh"
    COMPOSE_DIR = "/opt/biblio/managed-embedding-endpoint"

    def __init__(
        self,
        settings: Settings,
        infrastructure: Infrastructure,
        *,
        target_name: str,
        target_zone: str,
    ) -> None:
        self.settings = settings
        self.infrastructure = infrastructure
        self.target_name = target_name
        self.target_zone = target_zone

    def start(self, run_id: str) -> None:
        self._sync_tools()
        result_dir = f"$HOME/biblio-target-load-results/{run_id}"
        command = (
            f'result_dir="{result_dir}"; mkdir -p "$result_dir"; '
            f'nohup "$HOME/{self.REMOTE_SAMPLER}" --output-dir "$result_dir" '
            f"--compose-dir {self.COMPOSE_DIR} "
            f"--network-capacity-bps {self.settings.target_network_capacity_bps} "
            '> "$result_dir/sampler-console.log" 2>&1 </dev/null &'
        )
        self._target_ssh(command)
        self._wait_for_sampler(run_id)

    def stop(self, run_id: str) -> None:
        result_dir = f"$HOME/biblio-target-load-results/{run_id}"
        command = (
            f'result_dir="{result_dir}"; touch "$result_dir/stop"; '
            'for _ in $(seq 1 30); do test ! -e "$result_dir/sampler.pid" && break; '
            'sleep 1; done; test ! -e "$result_dir/sampler.pid"; '
            'test -s "$result_dir/target-metrics.json"'
        )
        self._target_ssh(command)

    def collect_evidence(
        self, run_id: str, model_version: str, trace_namespace: str
    ) -> None:
        command = (
            f'bash "$HOME/{self.REMOTE_EVIDENCE}" '
            f"{shlex.quote(run_id)} {shlex.quote(model_version)} "
            f"{shlex.quote(trace_namespace)}"
        )
        self._target_ssh(command)

    def _sync_tools(self) -> None:
        destination = f"{self.target_name}:~/"
        for local_path, remote_name in (
            (self.settings.target_vm_sampler, self.REMOTE_SAMPLER),
            (self.settings.remote_target_evidence, self.REMOTE_EVIDENCE),
        ):
            self.infrastructure.scp(
                str(local_path),
                f"{destination}{remote_name}",
                zone=self.target_zone,
            )
        self._target_ssh(f"chmod 0755 \"$HOME/{self.REMOTE_SAMPLER}\"")

    def _wait_for_sampler(self, run_id: str) -> None:
        pid_file = f"$HOME/biblio-target-load-results/{run_id}/sampler.pid"
        command = f'test -s "{pid_file}" && kill -0 "$(cat "{pid_file}")"'
        for _ in range(20):
            completed = self.infrastructure.ssh(
                self.target_name,
                self.target_zone,
                command,
                capture_output=True,
                check=False,
            )
            if completed.returncode == 0:
                return
            time.sleep(1)
        raise LoadTestError("Target VM sampler did not start.")

    def _target_ssh(self, command: str) -> None:
        self.infrastructure.ssh(
            self.target_name,
            self.target_zone,
            command,
        )
