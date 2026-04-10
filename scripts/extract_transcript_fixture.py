#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
WORKER_DIR = ROOT / "services" / "pipeline-worker"
DEFAULT_FIXTURE_DIR = SCRIPT_DIR / "e2e_fixtures" / "transcripts"

if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _default_output_path(video_path: Path) -> Path:
    return DEFAULT_FIXTURE_DIR / f"{video_path.stem}.json"


def _build_fixture_payload(video_path: Path, result: Any) -> dict[str, Any]:
    return {
        "source_video_path": str(video_path),
        "stt_model_version": result.stt_model_version,
        "segments": [
            {
                "segment_index": index,
                "text": segment.text,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
            }
            for index, segment in enumerate(result.segments)
        ],
    }


async def _extract_transcript_fixture(video_path: Path, output_path: Path) -> Path:
    from google.cloud import storage as gcs_storage

    from src.infra.ai.google_stt_adapter import GoogleSTTAdapter
    from src.infra.ai.stt_batch_callable import build_stt_callable
    from src.infra.media.ffmpeg_client import FFmpegClient
    from src.infra.storage.gcs_client import GCSStorageClient

    env = _read_env_file(WORKER_DIR / ".env")
    project_id = env["GCP_PROJECT_ID"]
    location = env.get("GCP_LOCATION", "us-central1")
    bucket_name = env["GCS_VIDEO_BUCKET_NAME"]
    recognizer = env.get("STT_RECOGNIZER", "")
    stt_model_version = env.get("STT_MODEL_VERSION", "") or "chirp_2"
    max_retries = int(env.get("MAX_RETRIES", "3"))
    submit_timeout_sec = int(env.get("STT_SUBMIT_TIMEOUT_SEC", "30"))
    operation_timeout_sec = int(env.get("STT_OPERATION_TIMEOUT_SEC", "900"))

    ffmpeg = FFmpegClient()
    gcs_client = gcs_storage.Client(project=project_id)
    storage_client = GCSStorageClient(
        bucket_factory=lambda: gcs_client.bucket(bucket_name),
        bucket_name=bucket_name,
    )
    stt_adapter = GoogleSTTAdapter(
        client=build_stt_callable(
            project_id=project_id,
            location=location,
            recognizer=recognizer,
            model=stt_model_version,
            submit_timeout_sec=submit_timeout_sec,
            operation_timeout_sec=operation_timeout_sec,
        ),
        max_retries=max_retries,
    )

    with tempfile.TemporaryDirectory(prefix="biblio-transcript-fixture-") as temp_dir:
        audio_path = Path(temp_dir) / "audio.flac"
        await asyncio.to_thread(ffmpeg.extract_audio, video_path, audio_path)

        object_prefix = uuid4()
        audio_storage_path = f"artifacts/transcript-fixtures/{object_prefix}/audio.flac"
        try:
            await storage_client.upload_object(audio_path, audio_storage_path)
            result = await stt_adapter.transcribe(
                audio_uri=storage_client.object_uri(audio_storage_path),
                trace_id=str(uuid4()),
            )
        finally:
            try:
                await storage_client.delete_object(audio_storage_path)
            except Exception:
                pass

    payload = _build_fixture_payload(video_path, result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract a reusable STT transcript fixture from a local video using the real Google STT flow."
    )
    parser.add_argument("--video-path", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.output is None:
        args.output = _default_output_path(args.video_path)
    return args


def main() -> int:
    args = parse_args()
    if not args.video_path.exists():
        print(f"Video not found: {args.video_path}", file=sys.stderr, flush=True)
        return 1
    output_path = asyncio.run(_extract_transcript_fixture(args.video_path, args.output))
    print(f"Transcript fixture written: {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
