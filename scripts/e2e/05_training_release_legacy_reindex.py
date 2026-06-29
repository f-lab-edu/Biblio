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


SCRIPT_NAME = "05_training_release_legacy_reindex"


def main() -> int:
    parser = common_parser("Enqueue training and verify release plus legacy reindex progress.")
    args = parser.parse_args()
    context = build_context(args)
    if context.dry_run:
        return dry_run_result(context, name=SCRIPT_NAME, observations=_dry_run_observations(context))
    started_at = utc_now()
    try:
        require_live_config(context.config, ["gcp.project_id", "postgres.instance_name"])
        before_release = _current_release(context)
        payload = _training_payload()
        context.postgres.execute(_enqueue_sql(context.config.queue_name("training"), payload))
        release_state = _wait_for_training_release(context, before_release)
        reindex_state = _legacy_reindex_state(context)
        return finish_step(
            context,
            name=SCRIPT_NAME,
            started_at=started_at,
            observations={
                "queue_payload": payload,
                "release_before": before_release,
                "release_after": release_state,
                "legacy_reindex": reindex_state,
            },
        )
    except Exception as exc:
        return finish_step(context, name=SCRIPT_NAME, started_at=started_at, observations={}, error=exc)


def _training_payload() -> dict[str, Any]:
    return {
        "message_type": "TRAINING_REQUEST",
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "issued_at": datetime.now(UTC).isoformat(),
    }


def _enqueue_sql(queue_name: str, payload: dict[str, Any]) -> str:
    return f"SELECT pgmq.send('{queue_name}', '{json_for_sql(payload)}'::jsonb);"


def _wait_for_training_release(context: Any, before_release: dict[str, str] | None) -> dict[str, str]:
    timeout = context.config.timeout_seconds("training_release", 7200.0)
    interval = context.config.timeout_seconds("poll_interval", 15.0)
    return poll_until(
        name="training release DEPLOY_COMPLETED",
        check=lambda: _release_completed_or_none(context, before_release),
        timeout_seconds=timeout,
        interval_seconds=interval,
    )


def _release_completed_or_none(context: Any, before_release: dict[str, str] | None) -> dict[str, str] | None:
    release = _current_release(context)
    latest_run = _latest_completed_run(context)
    if release is None or latest_run is None:
        return None
    active_changed = before_release is None or release["active_model_version"] != before_release["active_model_version"]
    if release["release_status"] == "STABLE" and active_changed:
        return {**release, **{"latest_run_id": latest_run["id"]}}
    return None


def _current_release(context: Any) -> dict[str, str] | None:
    rows = context.postgres.fetch_csv(_release_sql()).rows
    return rows[0] if rows else None


def _latest_completed_run(context: Any) -> dict[str, str] | None:
    rows = context.postgres.fetch_csv(_latest_completed_run_sql()).rows
    return rows[0] if rows else None


def _legacy_reindex_state(context: Any) -> list[dict[str, str]]:
    return context.postgres.fetch_csv(_legacy_reindex_sql()).rows


def _release_sql() -> str:
    return """
SELECT release_status, active_model_version, active_index_name, switched_at::text
FROM model_release
ORDER BY updated_at DESC
LIMIT 1
""".strip()


def _latest_completed_run_sql() -> str:
    return """
SELECT id::text, status, candidate_model_version, candidate_index_name
FROM ml_pipeline_run
WHERE status = 'DEPLOY_COMPLETED'
ORDER BY updated_at DESC
LIMIT 1
""".strip()


def _legacy_reindex_sql() -> str:
    return """
SELECT status, COUNT(*)::text AS count
FROM legacy_reindex_item
GROUP BY status
ORDER BY status
""".strip()


def _dry_run_observations(context: Any) -> dict[str, Any]:
    payload = _training_payload()
    return {
        "queue": context.config.queue_name("training"),
        "enqueue_sql": _enqueue_sql(context.config.queue_name("training"), payload),
        "release_sql": _release_sql(),
        "completed_run_sql": _latest_completed_run_sql(),
        "legacy_reindex_sql": _legacy_reindex_sql(),
    }


if __name__ == "__main__":
    script_entrypoint(main)
