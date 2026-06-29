from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class TestBackendE2EHelpers(unittest.TestCase):
    def test_config_loads_required_shared_identity(self) -> None:
        from scripts.e2e.lib.config import E2EConfig

        config_path = self._write_config(
            {
                "gcp": {"project_id": "project-1", "region": "asia-northeast3"},
                "services": {
                    "core_api_url": "https://core.example.run.app",
                    "search_service_url": "https://search.example.run.app",
                },
                "auth": {"jwt_secret_key": "secret"},
                "test_identity": {
                    "user_id": "11111111-1111-4111-8111-111111111111",
                    "project_id": "22222222-2222-4222-8222-222222222222",
                },
                "postgres": {
                    "instance_name": "biblio-postgres",
                    "zone": "asia-northeast3-a",
                    "database": "app",
                    "remote_psql_command": "sudo -u postgres psql",
                },
            }
        )

        config = E2EConfig.load(config_path)

        self.assertEqual(config.user_id, "11111111-1111-4111-8111-111111111111")
        self.assertEqual(config.project_id, "22222222-2222-4222-8222-222222222222")
        self.assertEqual(config.service_url("core_api"), "https://core.example.run.app")

    def test_jwt_contains_requester_and_admin_role(self) -> None:
        from scripts.e2e.lib.http import decode_unverified_payload, make_jwt

        token = make_jwt(
            requester_user_id="11111111-1111-4111-8111-111111111111",
            secret="secret",
            admin=True,
        )

        payload = decode_unverified_payload(token)

        self.assertEqual(payload["requester_user_id"], "11111111-1111-4111-8111-111111111111")
        self.assertEqual(payload["role"], "admin")

    def test_postgres_client_builds_iap_psql_command_without_running(self) -> None:
        from scripts.e2e.lib.config import E2EConfig
        from scripts.e2e.lib.postgres import PostgresClient

        config = E2EConfig(
            {
                "gcp": {"project_id": "project-1", "region": "asia-northeast3"},
                "postgres": {
                    "instance_name": "biblio-postgres",
                    "zone": "asia-northeast3-a",
                    "database": "app",
                    "remote_psql_command": "sudo -u postgres psql",
                },
                "test_identity": {
                    "user_id": "11111111-1111-4111-8111-111111111111",
                    "project_id": "22222222-2222-4222-8222-222222222222",
                },
            }
        )

        command = PostgresClient(config).build_command("SELECT 1")

        self.assertEqual(command[:4], ["gcloud", "compute", "ssh", "biblio-postgres"])
        self.assertIn("--tunnel-through-iap", command)
        self.assertIn("psql", command[-1])
        self.assertIn("SELECT 1", command[-1])

    def test_report_writer_persists_step_result(self) -> None:
        from scripts.e2e.lib.report import ReportWriter, StepResult

        run_dir = self._tmp_dir() / "report"
        writer = ReportWriter(run_dir=run_dir, started_at="2026-06-28T00:00:00Z")
        writer.add_step(
            StepResult(
                name="01_video_upload_to_ready",
                status="PASS",
                started_at="2026-06-28T00:00:00Z",
                finished_at="2026-06-28T00:00:01Z",
                observations={"video_count": 2},
            )
        )

        report_path = writer.write()
        payload = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["steps"][0]["observations"], {"video_count": 2})

    def test_poll_until_times_out_with_clear_message(self) -> None:
        from scripts.e2e.lib.polling import PollTimeoutError, poll_until

        with self.assertRaisesRegex(PollTimeoutError, "never-ready"):
            poll_until(
                name="never-ready",
                check=lambda: None,
                timeout_seconds=0.01,
                interval_seconds=0.01,
                sleep=lambda _: None,
            )

    def _write_config(self, payload: dict[str, object]) -> Path:
        config_path = self._tmp_dir() / "config.json"
        config_path.write_text(json.dumps(payload), encoding="utf-8")
        return config_path

    def _tmp_dir(self) -> Path:
        import tempfile

        return Path(tempfile.mkdtemp())


if __name__ == "__main__":
    unittest.main()
