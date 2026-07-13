from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.e2e.lib.polling import poll_until
from scripts.e2e.lib.scenario import build_context, common_parser, dry_run_result, finish_step, json_for_sql, require_live_config, script_entrypoint
from scripts.e2e.lib.report import utc_now


SCRIPT_NAME = "06_rollback_recovery"


def main() -> int:
    parser = common_parser("Trigger admin rollback and verify rollback recovery signals.")
    parser.add_argument("--use-direct-queue-fallback", action="store_true")
    args = parser.parse_args()
    context = build_context(args)
    if context.dry_run:
        return dry_run_result(context, name=SCRIPT_NAME, observations=_dry_run_observations(context, args))
    started_at = utc_now()
    try:
        require_live_config(
            context.config,
            ["gcp.project_id", "services.core_api_url", "auth.jwt_secret_key", "postgres.instance_name"],
        )
        before_release = _require_current_release(context)
        trigger_result = _trigger_rollback(context, before_release, args.use_direct_queue_fallback)
        rollback_state = _wait_for_rollback(context, before_release)
        recovery_state = _recovery_state(context)
        return finish_step(
            context,
            name=SCRIPT_NAME,
            started_at=started_at,
            observations={
                "trigger": trigger_result,
                "release_before": before_release,
                "rollback_state": rollback_state,
                "recovery_state": recovery_state,
            },
        )
    except Exception as exc:
        return finish_step(context, name=SCRIPT_NAME, started_at=started_at, observations={}, error=exc)


def _trigger_rollback(context: Any, before_release: dict[str, str], use_fallback: bool) -> dict[str, Any]:
    try:
        url = f"{context.config.service_url('core_api')}/api/v1/admin/model-release/rollback"
        response = context.admin_http.post_json(url, {})
        return {"path": "admin_api", "response": response}
    except Exception:
        if not use_fallback:
            raise
    payload = _rollback_payload(before_release)
    context.postgres.execute(_enqueue_sql(context.config.queue_name("rollback"), payload))
    return {"path": "direct_queue_fallback", "payload": payload}


def _rollback_payload(before_release: dict[str, str]) -> dict[str, Any]:
    switched_at = before_release.get("switched_at")
    active_model_version = before_release.get("active_model_version")
    if not switched_at or not active_model_version:
        raise RuntimeError("Cannot build ROLLBACK_REQUEST without active_model_version and switched_at.")
    return {
        "message_type": "ROLLBACK_REQUEST",
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "issued_at": datetime.now(UTC).isoformat(),
        "expected_active_model_version": active_model_version,
        "expected_switched_at": switched_at,
    }


def _enqueue_sql(queue_name: str, payload: dict[str, Any]) -> str:
    return f"SELECT pgmq.send('{queue_name}', '{json_for_sql(payload)}'::jsonb);"


def _wait_for_rollback(context: Any, before_release: dict[str, str]) -> dict[str, str]:
    timeout = context.config.timeout_seconds("rollback_recovery", 1800.0)
    interval = context.config.timeout_seconds("poll_interval", 15.0)
    return poll_until(
        name="model rollback completion",
        check=lambda: _rollback_complete_or_none(context, before_release),
        timeout_seconds=timeout,
        interval_seconds=interval,
    )


def _rollback_complete_or_none(context: Any, before_release: dict[str, str]) -> dict[str, str] | None:
    release = _require_current_release(context)
    snapshot = _rolled_back_snapshot(context)
    active_changed = release["active_model_version"] != before_release["active_model_version"]
    if release["release_status"] == "STABLE" and active_changed and snapshot is not None:
        return {**release, "rolled_back_snapshot_id": snapshot["snapshot_id"]}
    return None


def _require_current_release(context: Any) -> dict[str, str]:
    rows = context.postgres.fetch_csv(_release_sql()).rows
    if not rows:
        raise RuntimeError("model_release row is required before rollback.")
    return rows[0]


def _rolled_back_snapshot(context: Any) -> dict[str, str] | None:
    rows = context.postgres.fetch_csv(_rolled_back_snapshot_sql()).rows
    return rows[0] if rows else None


def _recovery_state(context: Any) -> dict[str, Any]:
    return {
        "project": _project_state(context),
        "vector_entry_count": _project_vector_count(context),
        "reembedding_queue": _reembedding_queue_state(context),
    }


def _project_state(context: Any) -> dict[str, str] | None:
    rows = context.postgres.fetch_csv(_project_state_sql(context.config.project_id)).rows
    return rows[0] if rows else None


def _project_vector_count(context: Any) -> dict[str, str] | None:
    rows = context.postgres.fetch_csv(_project_vector_count_sql(context.config.project_id)).rows
    return rows[0] if rows else None


def _reembedding_queue_state(context: Any) -> dict[str, str] | str:
    table_name = context.config.get("queues.reembedding_table_name")
    if not isinstance(table_name, str) or not table_name:
        return "not_checked: set queues.reembedding_table_name if this deployment exposes a stable PGMQ table name"
    rows = context.postgres.fetch_csv(f"SELECT COUNT(*)::text AS count FROM {table_name}").rows
    return rows[0] if rows else {"count": "0"}


def _release_sql() -> str:
    return """
SELECT
  release_status,
  active_model_version,
  active_index_name,
  previous_model_version,
  previous_index_name,
  switched_at::text
FROM model_release
ORDER BY updated_at DESC
LIMIT 1
""".strip()


def _rolled_back_snapshot_sql() -> str:
    return """
SELECT snapshot_id::text, model_version, index_name, status
FROM model_snapshot
WHERE status = 'ROLLED_BACK'
ORDER BY created_at DESC
LIMIT 1
""".strip()


def _project_state_sql(project_id: str) -> str:
    return f"""
SELECT id::text, search_serving_state
FROM project
WHERE id = '{project_id}'::uuid
""".strip()


def _project_vector_count_sql(project_id: str) -> str:
    return f"""
SELECT COUNT(*)::text AS count
FROM vector_index_entry
WHERE project_id = '{project_id}'::uuid
""".strip()


def _dry_run_observations(context: Any, args: Any) -> dict[str, Any]:
    sample_release = {
        "active_model_version": "active-model",
        "switched_at": "2026-06-28T00:00:00+00:00",
    }
    return {
        "admin_url": f"{context.config.service_url('core_api')}/api/v1/admin/model-release/rollback",
        "fallback_enabled": bool(args.use_direct_queue_fallback),
        "fallback_queue": context.config.queue_name("rollback"),
        "fallback_enqueue_sql": _enqueue_sql(context.config.queue_name("rollback"), _rollback_payload(sample_release)),
        "release_sql": _release_sql(),
        "snapshot_sql": _rolled_back_snapshot_sql(),
        "project_state_sql": _project_state_sql(context.config.project_id),
    }


if __name__ == "__main__":
    script_entrypoint(main)
