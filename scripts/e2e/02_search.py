from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.e2e.lib.polling import poll_until
from scripts.e2e.lib.scenario import build_context, common_parser, dry_run_result, finish_step, require_live_config, script_entrypoint
from scripts.e2e.lib.report import utc_now


SCRIPT_NAME = "02_search"


def main() -> int:
    parser = common_parser("Call search-service and verify feedback snapshot persistence.")
    parser.add_argument("--query", default=None)
    args = parser.parse_args()
    context = build_context(args)
    query = args.query or context.config.optional_str("search.query", "backend e2e")
    if context.dry_run:
        return dry_run_result(context, name=SCRIPT_NAME, observations=_dry_run_observations(context, query))
    started_at = utc_now()
    try:
        require_live_config(
            context.config,
            ["gcp.project_id", "services.search_service_url", "auth.jwt_secret_key", "postgres.instance_name"],
        )
        response = run_search(context, query)
        snapshot = _wait_for_snapshot(context, str(response["req_id"]))
        return finish_step(
            context,
            name=SCRIPT_NAME,
            started_at=started_at,
            observations={"search_response": response, "snapshot": snapshot},
        )
    except Exception as exc:
        return finish_step(context, name=SCRIPT_NAME, started_at=started_at, observations={}, error=exc)


def run_search(context: Any, query: str) -> dict[str, Any]:
    url = f"{context.config.service_url('search_service')}/api/v1/search"
    response = context.http.post_json(url, {"project_id": context.config.project_id, "query": query})
    if response is None or "req_id" not in response or "chunks" not in response:
        raise RuntimeError(f"Search response is missing required fields: {response!r}")
    return response


def _wait_for_snapshot(context: Any, req_id: str) -> dict[str, str]:
    return poll_until(
        name="search_response_snapshot row",
        check=lambda: _snapshot_or_none(context, req_id),
        timeout_seconds=60,
        interval_seconds=5,
    )


def _snapshot_or_none(context: Any, req_id: str) -> dict[str, str] | None:
    rows = context.postgres.fetch_csv(_snapshot_sql(req_id)).rows
    return rows[0] if rows else None


def _snapshot_sql(req_id: str) -> str:
    return f"""
SELECT
  req_id::text,
  user_id::text,
  project_id::text,
  query_text,
  active_model_version,
  active_index_name
FROM search_response_snapshot
WHERE req_id = '{req_id}'::uuid
""".strip()


def _dry_run_observations(context: Any, query: str) -> dict[str, Any]:
    return {
        "search_url": f"{context.config.service_url('search_service')}/api/v1/search",
        "request_body": {"project_id": context.config.project_id, "query": query},
        "snapshot_sql": _snapshot_sql("00000000-0000-4000-8000-000000000000"),
    }


if __name__ == "__main__":
    script_entrypoint(main)
