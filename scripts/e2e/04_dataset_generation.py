from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.e2e.lib.polling import poll_until
from scripts.e2e.lib.gcloud import GCloudError
from scripts.e2e.lib.scenario import build_context, common_parser, dry_run_result, finish_step, json_for_sql, require_live_config, script_entrypoint
from scripts.e2e.lib.report import utc_now


SCRIPT_NAME = "04_dataset_generation"


def main() -> int:
    parser = common_parser("Enqueue dataset generation and verify a dataset manifest.")
    args = parser.parse_args()
    context = build_context(args)
    if context.dry_run:
        return dry_run_result(context, name=SCRIPT_NAME, observations=_dry_run_observations(context))
    started_at = utc_now()
    try:
        require_live_config(context.config, _live_paths())
        payload = _dataset_payload()
        context.postgres.execute(_enqueue_sql(context.config.queue_name("dataset"), payload))
        manifest_objects = _wait_for_manifest(context)
        return finish_step(
            context,
            name=SCRIPT_NAME,
            started_at=started_at,
            observations={"queue_payload": payload, "manifest_objects": manifest_objects[:20]},
        )
    except Exception as exc:
        return finish_step(context, name=SCRIPT_NAME, started_at=started_at, observations={}, error=exc)


def _dataset_payload() -> dict[str, Any]:
    return {
        "message_type": "DATASET_GENERATION_REQUEST",
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "issued_at": datetime.now(UTC).isoformat(),
    }


def _enqueue_sql(queue_name: str, payload: dict[str, Any]) -> str:
    return f"SELECT pgmq.send('{queue_name}', '{json_for_sql(payload)}'::jsonb);"


def _wait_for_manifest(context: Any) -> list[str]:
    timeout = context.config.timeout_seconds("dataset_generation", 900.0)
    interval = context.config.timeout_seconds("poll_interval", 15.0)
    return poll_until(
        name="dataset manifest object",
        check=lambda: _manifest_objects_or_none(context),
        timeout_seconds=timeout,
        interval_seconds=interval,
    )


def _manifest_objects_or_none(context: Any) -> list[str] | None:
    bucket = context.config.required_str("storage.ml_artifact_bucket")
    prefix = context.config.optional_str("storage.dataset_artifact_prefix", "feedback/datasets")
    try:
        objects = context.gcloud.storage_ls(f"gs://{bucket}/{prefix}/**/manifest.json")
    except GCloudError:
        return None
    return objects or None


def _dry_run_observations(context: Any) -> dict[str, Any]:
    payload = _dataset_payload()
    return {
        "queue": context.config.queue_name("dataset"),
        "enqueue_sql": _enqueue_sql(context.config.queue_name("dataset"), payload),
        "manifest_glob": f"gs://{context.config.get('storage.ml_artifact_bucket', '<bucket>')}/"
        f"{context.config.optional_str('storage.dataset_artifact_prefix', 'feedback/datasets')}/**/manifest.json",
    }


def _live_paths() -> list[str]:
    return ["gcp.project_id", "postgres.instance_name", "storage.ml_artifact_bucket"]


if __name__ == "__main__":
    script_entrypoint(main)
