from __future__ import annotations

import argparse
import base64
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.e2e.lib.config import E2EConfig, default_config_path
from scripts.e2e.lib.gcloud import GCloud
from scripts.e2e.lib.http import JsonHttpClient, make_jwt
from scripts.e2e.lib.postgres import PostgresClient
from scripts.e2e.lib.report import ReportWriter, StepResult, timestamp_for_path, utc_now


@dataclass(frozen=True)
class ScenarioContext:
    config: E2EConfig
    gcloud: GCloud
    postgres: PostgresClient
    http: JsonHttpClient
    admin_http: JsonHttpClient
    report: ReportWriter
    dry_run: bool


def common_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--report-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def build_context(args: argparse.Namespace) -> ScenarioContext:
    config = E2EConfig.load(args.config)
    gcloud = GCloud(config)
    use_id_token = bool(config.get("auth.use_cloud_run_identity_token", False))
    http_timeout = config.timeout_seconds("http", 30.0)
    report_dir = args.report_dir or _default_report_dir(config)
    app_jwt = make_jwt(
        requester_user_id=config.user_id,
        secret=config.jwt_secret_key,
        admin=False,
    )
    admin_jwt = make_jwt(
        requester_user_id=config.user_id,
        secret=config.jwt_secret_key,
        admin=True,
    )
    return ScenarioContext(
        config=config,
        gcloud=gcloud,
        postgres=PostgresClient(config),
        http=JsonHttpClient(
            app_jwt=app_jwt,
            gcloud=gcloud,
            use_cloud_run_identity_token=use_id_token,
            timeout_seconds=http_timeout,
        ),
        admin_http=JsonHttpClient(
            app_jwt=admin_jwt,
            gcloud=gcloud,
            use_cloud_run_identity_token=use_id_token,
            timeout_seconds=http_timeout,
        ),
        report=ReportWriter(run_dir=report_dir),
        dry_run=bool(args.dry_run),
    )


def finish_step(
    context: ScenarioContext,
    *,
    name: str,
    started_at: str,
    observations: dict[str, Any],
    error: Exception | None = None,
) -> int:
    status = "FAIL" if error else "PASS"
    context.report.add_step(
        StepResult(
            name=name,
            status=status,
            started_at=started_at,
            finished_at=utc_now(),
            observations=observations,
            error=str(error) if error else None,
        )
    )
    report_path = context.report.write()
    _print_summary(name, status, report_path, observations, error)
    return 1 if error else 0


def dry_run_result(context: ScenarioContext, *, name: str, observations: dict[str, Any]) -> int:
    started_at = utc_now()
    observations = {"dry_run": True, **observations}
    return finish_step(context, name=name, started_at=started_at, observations=observations)


def require_live_config(config: E2EConfig, paths: list[str]) -> None:
    for path in paths:
        config.require_live_value(path)


def sample_video_payload(config: E2EConfig) -> bytes:
    encoded = config.required_str("video_upload.sample_payload_base64")
    return base64.b64decode(encoded)


def json_for_sql(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).replace("'", "''")


def script_entrypoint(main: Any) -> None:
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130) from None


def _default_report_dir(config: E2EConfig) -> Path:
    root = Path(config.optional_str("artifacts.root", "artifacts/e2e"))
    return root / timestamp_for_path()


def _print_summary(
    name: str,
    status: str,
    report_path: Path,
    observations: dict[str, Any],
    error: Exception | None,
) -> None:
    print(f"{name}: {status}")
    for key, value in observations.items():
        print(f"  {key}: {value}")
    if error is not None:
        print(f"  error: {error}")
    print(f"  report: {report_path}")
