#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import e2e_backend_smoke_shared as SHARED


StepError = SHARED.StepError
StepTimer = SHARED.StepTimer
_print_step = SHARED._print_step
_run_timed = SHARED._run_timed
_run_search_queries = SHARED._run_search_queries
_validate_video_paths = SHARED._validate_video_paths
_load_scenario = SHARED._load_scenario
_prepare_compose_smoke = SHARED._prepare_compose_smoke
_seed_transcript_fixture_via_compose = SHARED._seed_transcript_fixture_via_compose


def run_smoke(args: argparse.Namespace) -> int:
    _validate_video_paths([args.video_path])
    if not args.transcript_fixture_path.exists():
        raise StepError(f"Transcript fixture not found: {args.transcript_fixture_path}")

    timer = StepTimer()
    log_dir, token = _prepare_compose_smoke(
        timer=timer,
        log_prefix="biblio-e2e-transcript-logs-",
        core_api_base_url=args.core_api_base_url,
        embedding_base_url=args.embedding_base_url,
        search_base_url=args.search_base_url,
        user_id=args.user_id,
    )

    _print_step("Create source video")
    video_id, signed_url = _run_timed(
        timer,
        "video_create_1",
        SHARED._create_local_file_video,
        token,
        args.video_path,
        1,
    )

    _print_step("Upload source video")
    upload_status = _run_timed(
        timer,
        "video_upload_1",
        SHARED._upload_file,
        signed_url,
        args.video_path,
        args.upload_timeout_sec,
    )
    if upload_status != 200:
        raise StepError(f"Video upload failed: status={upload_status}, video_id={video_id}")

    _print_step("Seed transcript fixture")
    _run_timed(
        timer,
        "transcript_seed_1",
        _seed_transcript_fixture_via_compose,
        video_id=video_id,
        fixture_path=args.transcript_fixture_path,
    )

    _print_step("Complete upload")
    _run_timed(timer, "video_complete_1", SHARED._complete_local_file_video, token, video_id)

    _print_step("Wait for READY")
    _run_timed(
        timer,
        "video_ready_poll_1",
        SHARED._poll_video_ready,
        token,
        video_id,
        timeout_sec=args.ready_timeout_sec,
    )

    _run_search_queries(timer=timer, token=token, queries=args.queries, log_dir=log_dir)
    print("\nTranscript fixture Docker E2E smoke succeeded.", flush=True)
    print(timer.summary(), flush=True)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run downstream backend E2E using a precomputed STT transcript fixture.")
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--video-path", type=Path, default=None)
    parser.add_argument("--transcript-fixture-path", type=Path, default=None)
    parser.add_argument("--query", dest="queries", action="append")
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--ready-timeout-sec", type=int, default=None)
    parser.add_argument("--upload-timeout-sec", type=int, default=None)
    parser.add_argument("--core-api-base-url", default=SHARED.DEFAULT_CORE_API_BASE_URL)
    parser.add_argument("--embedding-base-url", default=SHARED.DEFAULT_EMBEDDING_BASE_URL)
    parser.add_argument("--search-base-url", default=SHARED.DEFAULT_SEARCH_BASE_URL)
    args = parser.parse_args(argv)

    scenario: dict[str, object] = {}
    if args.scenario is not None:
        scenario = _load_scenario(args.scenario)

    if args.video_path is None:
        raw_video_path = scenario.get("video_path")
        if raw_video_path is None:
            parser.error("--video-path or scenario.video_path is required")
        args.video_path = Path(str(raw_video_path))

    if args.transcript_fixture_path is None:
        raw_fixture_path = scenario.get("transcript_fixture_path")
        if raw_fixture_path is None:
            parser.error("--transcript-fixture-path or scenario.transcript_fixture_path is required")
        args.transcript_fixture_path = Path(str(raw_fixture_path))

    if not args.queries:
        args.queries = list(scenario.get("queries", SHARED.DEFAULT_QUERIES))
    if args.user_id is None:
        args.user_id = str(scenario.get("user_id", SHARED.DEFAULT_USER_ID))
    if args.ready_timeout_sec is None:
        args.ready_timeout_sec = int(scenario.get("ready_timeout_sec", 1800))
    if args.upload_timeout_sec is None:
        args.upload_timeout_sec = int(scenario.get("upload_timeout_sec", SHARED.DEFAULT_UPLOAD_TIMEOUT_SEC))
    return args


def main() -> int:
    try:
        return run_smoke(parse_args())
    except StepError as exc:
        print(f"\nTranscript fixture Docker E2E smoke failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
