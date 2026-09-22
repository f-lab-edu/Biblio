from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any

from scripts.e2e.lib.config import E2EConfig
from scripts.test_support.cloud_auth import user_identity_token_command


class GCloudError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    stdout: str
    stderr: str


class GCloud:
    def __init__(self, config: E2EConfig) -> None:
        self._config = config

    def identity_token(self, _audience: str) -> str:
        return run_command(user_identity_token_command()).stdout.strip()

    def describe_cloud_run_service_url(self, service_name: str) -> str:
        command = [
            "gcloud",
            "run",
            "services",
            "describe",
            service_name,
            f"--project={self._config.gcp_project_id}",
            f"--region={self._config.region}",
            "--format=json",
        ]
        payload = _loads_json(run_command(command).stdout)
        status = payload.get("status", {})
        if not isinstance(status, dict) or not isinstance(status.get("url"), str):
            raise GCloudError(f"Cloud Run service URL not found: {service_name}")
        return status["url"]

    def storage_ls(self, uri: str) -> list[str]:
        command = ["gcloud", "storage", "ls", uri]
        result = run_command(command)
        return [line for line in result.stdout.splitlines() if line.strip()]


def run_command(command: list[str], *, input_text: str | None = None) -> CommandResult:
    completed = subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        check=False,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        joined = " ".join(command)
        raise GCloudError(f"Command failed: {joined}\n{completed.stderr.strip()}")
    return CommandResult(command=command, stdout=completed.stdout, stderr=completed.stderr)


def _loads_json(payload: str) -> dict[str, Any]:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise GCloudError("gcloud returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise GCloudError("gcloud JSON response must be an object.")
    return parsed
