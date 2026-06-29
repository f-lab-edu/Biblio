from __future__ import annotations

import sys
import importlib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.e2e.lib.polling import poll_until
from scripts.e2e.lib.gcloud import GCloudError
from scripts.e2e.lib.scenario import build_context, common_parser, dry_run_result, finish_step, require_live_config, script_entrypoint
from scripts.e2e.lib.report import utc_now


SCRIPT_NAME = "03_feedback_delivery"


def main() -> int:
    parser = common_parser("Submit feedback and verify raw feedback logs in GCS.")
    parser.add_argument("--req-id", default=None)
    args = parser.parse_args()
    context = build_context(args)
    if context.dry_run:
        return dry_run_result(context, name=SCRIPT_NAME, observations=_dry_run_observations(context, args.req_id))
    started_at = utc_now()
    try:
        require_live_config(context.config, _live_paths())
        req_id = args.req_id or _create_search_req_id(context)
        _submit_feedback(context, req_id)
        raw_logs = _wait_for_raw_logs(context)
        return finish_step(
            context,
            name=SCRIPT_NAME,
            started_at=started_at,
            observations={"req_id": req_id, "raw_log_objects": raw_logs[:20]},
        )
    except Exception as exc:
        return finish_step(context, name=SCRIPT_NAME, started_at=started_at, observations={}, error=exc)


def _create_search_req_id(context: Any) -> str:
    search_script = importlib.import_module("scripts.e2e.02_search")
    response = search_script.run_search(context, context.config.optional_str("search.query", "backend e2e"))
    return str(response["req_id"])


def _submit_feedback(context: Any, req_id: str) -> None:
    url = f"{context.config.service_url('core_api')}/api/v1/feedbacks"
    rating = context.config.optional_str("feedback.rating", "LIKE")
    context.http.post_json(url, {"req_id": req_id, "rating": rating})


def _wait_for_raw_logs(context: Any) -> list[str]:
    timeout = context.config.timeout_seconds("feedback_delivery", 300.0)
    interval = context.config.timeout_seconds("poll_interval", 15.0)
    return poll_until(
        name="feedback raw log object",
        check=lambda: _raw_logs_or_none(context),
        timeout_seconds=timeout,
        interval_seconds=interval,
    )


def _raw_logs_or_none(context: Any) -> list[str] | None:
    bucket = context.config.required_str("storage.feedback_log_bucket")
    prefix = context.config.optional_str("storage.raw_feedback_log_prefix", "feedback/raw_logs")
    try:
        objects = context.gcloud.storage_ls(f"gs://{bucket}/{prefix}/**")
    except GCloudError:
        return None
    return objects or None


def _dry_run_observations(context: Any, req_id: str | None) -> dict[str, Any]:
    return {
        "feedback_url": f"{context.config.service_url('core_api')}/api/v1/feedbacks",
        "req_id_source": "argument" if req_id else "search-service",
        "gcs_prefix": f"gs://{context.config.get('storage.feedback_log_bucket', '<bucket>')}/"
        f"{context.config.optional_str('storage.raw_feedback_log_prefix', 'feedback/raw_logs')}/**",
    }


def _live_paths() -> list[str]:
    return [
        "gcp.project_id",
        "services.core_api_url",
        "services.search_service_url",
        "auth.jwt_secret_key",
        "storage.feedback_log_bucket",
    ]


if __name__ == "__main__":
    script_entrypoint(main)
