from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from infrastructure import LoadTestError, Settings


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
        source: str,
        destination: str,
        *,
        zone: str,
        recursive: bool = False,
    ) -> None:
        self.scp_calls += 1
        self.last_source = source
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
        (result / "target-samples.tsv").write_text(
            "timestamp\tcpu\n1\t50\n", encoding="utf-8"
        )
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
