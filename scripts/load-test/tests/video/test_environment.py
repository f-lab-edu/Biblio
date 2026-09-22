from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


LOAD_TEST_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOAD_TEST_DIR))

from infrastructure import LoadTestError
from video_pipeline.environment import resolve_video_run_environment


PROJECT_ID = "6a03f8b0-d5e5-4a74-b611-c2d0fa4c47fa"
OWNER_ID = "6044a99a-5619-46c5-be2a-ddffc059273c"


class _Commands:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def output(self, command: list[str]) -> str:
        self.commands.append(command)
        if "list" in command:
            return "perf-database-url\nperf-jwt-secret-key"
        if "--secret=perf-jwt-secret-key" in command:
            return "jwt-secret"
        if "--secret=perf-database-url" in command:
            return "postgresql+asyncpg://user:password@10.0.0.4:5432/app"
        raise AssertionError(f"Unexpected command: {command}")


class _Infrastructure:
    project_id = "gcp-project"

    def terraform_output(self, name: str) -> str:
        if name != "core_api_url":
            raise AssertionError(name)
        return "https://core-api.example.test/"

    def instance_name_by_private_ip(self, private_ip: str) -> str:
        if private_ip != "10.0.0.4":
            raise AssertionError(private_ip)
        return "postgres-vm"

    def instance_zone_by_name(self, name: str) -> str:
        if name != "postgres-vm":
            raise AssertionError(name)
        return "test-zone"

    def ssh_output(self, name: str, zone: str, command: str) -> str:
        self.ssh_call = (name, zone, command)
        return OWNER_ID


class TestVideoRunEnvironment(unittest.TestCase):
    def test_resolves_runtime_values_from_gcp_and_project_owner(self) -> None:
        commands = _Commands()
        infrastructure = _Infrastructure()

        with patch.dict("os.environ", {}, clear=True):
            environment = resolve_video_run_environment(
                commands,
                infrastructure,
                biblio_project_id=PROJECT_ID,
                requested_gcp_project_id=None,
            )

        self.assertEqual(environment.gcp_project_id, "gcp-project")
        self.assertEqual(environment.core_api_url, "https://core-api.example.test")
        self.assertEqual(environment.requester_user_id, OWNER_ID)
        self.assertEqual(environment.app_jwt_secret, "jwt-secret")
        self.assertIn("FROM project", infrastructure.ssh_call[2])
        self.assertEqual(
            sum("list" in command for command in commands.commands),
            1,
        )

    def test_environment_overrides_avoid_secret_and_database_queries(self) -> None:
        commands = _Commands()
        infrastructure = _Infrastructure()
        configured = {
            "GCP_PROJECT_ID": "override-project",
            "CORE_API_URL": "https://override.example.test/",
            "REQUESTER_USER_ID": OWNER_ID,
            "APP_JWT_SECRET": "override-secret",
        }

        with patch.dict("os.environ", configured, clear=True):
            environment = resolve_video_run_environment(
                commands,
                infrastructure,
                biblio_project_id=PROJECT_ID,
                requested_gcp_project_id=None,
            )

        self.assertEqual(environment.gcp_project_id, "override-project")
        self.assertEqual(environment.core_api_url, "https://override.example.test")
        self.assertEqual(environment.app_jwt_secret, "override-secret")
        self.assertEqual(commands.commands, [])

    def test_rejects_ambiguous_secret_names(self) -> None:
        commands = _Commands()
        commands.output = lambda _command: (
            "first-jwt-secret-key\nsecond-jwt-secret-key"
        )

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(LoadTestError, "found 2"):
                resolve_video_run_environment(
                    commands,
                    _Infrastructure(),
                    biblio_project_id=PROJECT_ID,
                    requested_gcp_project_id=None,
                )

    def test_rejects_non_uuid_biblio_project_id(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(LoadTestError, "project ID is not a UUID"):
                resolve_video_run_environment(
                    _Commands(),
                    _Infrastructure(),
                    biblio_project_id="not-a-uuid",
                    requested_gcp_project_id=None,
                )


if __name__ == "__main__":
    unittest.main()
