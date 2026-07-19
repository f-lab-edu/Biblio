from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.e2e.lib.polling import poll_until
from scripts.e2e.lib.scenario import (
    build_context,
    common_parser,
    dry_run_result,
    finish_step,
    require_live_config,
    sample_video_payload,
    script_entrypoint,
)
from scripts.e2e.lib.report import utc_now


SCRIPT_NAME = "01_video_upload_to_ready"


def main() -> int:
    parser = common_parser("Upload videos and wait until backend processing reaches READY.")
    args = parser.parse_args()
    context = build_context(args)
    if context.dry_run:
        return dry_run_result(context, name=SCRIPT_NAME, observations=_dry_run_observations(context))
    started_at = utc_now()
    try:
        require_live_config(context.config, _live_paths())
        video_ids = _create_upload_and_complete(context)
        ready_rows = _wait_for_ready(context, video_ids)
        return finish_step(
            context,
            name=SCRIPT_NAME,
            started_at=started_at,
            observations={"video_ids": video_ids, "ready_rows": ready_rows},
        )
    except Exception as exc:
        return finish_step(context, name=SCRIPT_NAME, started_at=started_at, observations={}, error=exc)


def _create_upload_and_complete(context: Any) -> list[str]:
    count = int(context.config.get("video_upload.count", 1))
    video_ids: list[str] = []
    payload = sample_video_payload(context.config)
    for index in range(count):
        response = _create_video(context, index)
        video_id = str(response["video_id"])
        context.http.put_bytes(
            str(response["signed_url"]),
            payload,
            content_type="application/octet-stream",
        )
        _complete_video(context, video_id, len(payload))
        video_ids.append(video_id)
    return video_ids


def _create_video(context: Any, index: int) -> dict[str, Any]:
    url = f"{context.config.service_url('core_api')}/api/v1/projects/{context.config.project_id}/videos"
    body = {
        "title": f"{context.config.optional_str('video_upload.title_prefix', 'backend-e2e')}-{index + 1}",
        "category": context.config.optional_str("video_upload.category", "GENERAL"),
        "input_type": "LOCAL_FILE",
        "extension": context.config.optional_str("video_upload.extension", ".mp4"),
    }
    response = context.http.post_json(url, body)
    if response is None or "video_id" not in response or "signed_url" not in response:
        raise RuntimeError(f"Create video response is missing required fields: {response!r}")
    return response


def _complete_video(context: Any, video_id: str, size_bytes: int) -> None:
    url = f"{context.config.service_url('core_api')}/api/v1/videos/{video_id}/complete"
    response = context.http.post_json(url, {"size_bytes": size_bytes})
    if response is None or str(response.get("video_id")) != video_id:
        raise RuntimeError(f"Complete video response does not match video_id: {response!r}")


def _wait_for_ready(context: Any, video_ids: list[str]) -> list[dict[str, str]]:
    timeout = context.config.timeout_seconds("video_ready", 1800.0)
    interval = context.config.timeout_seconds("poll_interval", 15.0)
    return poll_until(
        name="videos READY with chunks and vectors",
        check=lambda: _ready_rows_or_none(context, video_ids),
        timeout_seconds=timeout,
        interval_seconds=interval,
    )


def _ready_rows_or_none(context: Any, video_ids: list[str]) -> list[dict[str, str]] | None:
    rows = context.postgres.fetch_csv(_video_ready_sql(video_ids)).rows
    if len(rows) != len(video_ids):
        return None
    for row in rows:
        chunk_count = int(row["chunk_count"])
        if (
            row["status"] != "READY"
            or chunk_count < 1
            or int(row["vector_count"]) != chunk_count
            or int(row["active_vector_count"]) != chunk_count
            or int(row["non_active_vector_count"]) != 0
        ):
            return None
    return rows


def _video_ready_sql(video_ids: list[str]) -> str:
    quoted_ids = ", ".join(f"'{video_id}'::uuid" for video_id in video_ids)
    return f"""
SELECT
  v.id::text AS video_id,
  v.status,
  COUNT(DISTINCT c.id)::text AS chunk_count,
  COUNT(vie.chunk_id)::text AS vector_count,
  COUNT(vie.chunk_id) FILTER (
    WHERE vie.index_name = mr.active_index_name
      AND vie.embedding_model_version = mr.active_model_version
  )::text AS active_vector_count,
  COUNT(vie.chunk_id) FILTER (
    WHERE vie.index_name <> mr.active_index_name
       OR vie.embedding_model_version <> mr.active_model_version
  )::text AS non_active_vector_count
FROM video v
CROSS JOIN model_release mr
LEFT JOIN chunk c ON c.video_id = v.id
LEFT JOIN vector_index_entry vie ON vie.video_id = v.id AND vie.chunk_id = c.id
WHERE v.id IN ({quoted_ids})
  AND mr.singleton_key = 1
GROUP BY v.id, v.status, mr.active_index_name, mr.active_model_version
ORDER BY v.id
""".strip()


def _dry_run_observations(context: Any) -> dict[str, Any]:
    return {
        "create_url": f"{context.config.service_url('core_api')}/api/v1/projects/{context.config.project_id}/videos",
        "complete_url_template": f"{context.config.service_url('core_api')}/api/v1/videos/<video_id>/complete",
        "video_count": context.config.get("video_upload.count", 1),
        "poll_sql": _video_ready_sql(["00000000-0000-4000-8000-000000000000"]),
    }


def _live_paths() -> list[str]:
    return [
        "gcp.project_id",
        "services.core_api_url",
        "auth.jwt_secret_key",
        "postgres.instance_name",
    ]


if __name__ == "__main__":
    script_entrypoint(main)
